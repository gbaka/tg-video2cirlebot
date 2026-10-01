"""
Подписки: активация, продление, отмена, истечение
"""

from datetime import UTC, datetime, timedelta

from repositories.base import LIFETIME_EXPIRES, Base, is_lifetime
from timeutil import now


class SubscriptionRepo(Base):
    async def get_active(self, user_id: int) -> dict | None:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND active = 1 "
                "AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
                (user_id, now()),
            )
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def activate(
        self,
        user_id: int,
        plan: str,
        days: int,
        source: str,
        charge_id: str | None = None,
        lifetime: bool = False,
    ) -> str:
        """Serialize the read and grant in one write transaction."""
        conn = await self._conn()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            expires = await self.activate_in_transaction(
                conn, user_id, plan, days, source, charge_id, lifetime
            )
            await conn.commit()
            return expires
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    @staticmethod
    async def activate_in_transaction(
        conn,
        user_id: int,
        plan: str,
        days: int,
        source: str,
        charge_id: str | None = None,
        lifetime: bool = False,
    ) -> str:
        """Caller MUST own a BEGIN IMMEDIATE transaction; this never commits."""
        cur = await conn.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id = ? AND active = 1 "
            "AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
            (user_id, now()),
        )
        row = await cur.fetchone()
        if row and is_lifetime(row["expires_at"]):
            return row["expires_at"]
        base = (
            datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
            if row
            else (datetime.now(UTC).replace(tzinfo=None))
        )
        expires = (
            LIFETIME_EXPIRES
            if lifetime
            else (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        )
        await conn.execute(
            "UPDATE subscriptions SET active = 0, "
            "cancelled_at = CASE WHEN expires_at <= ? THEN ? ELSE cancelled_at END, "
            "cancel_reason = CASE WHEN expires_at <= ? THEN 'expired' ELSE cancel_reason END "
            "WHERE user_id = ? AND active = 1",
            (now(), now(), now(), user_id),
        )
        await conn.execute(
            "INSERT INTO subscriptions (user_id, plan, expires_at, source, charge_id, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (user_id, plan, expires, source, charge_id),
        )
        return expires

    async def cancel(self, user_id: int, reason: str = "user") -> dict | None:
        """
        Отменяет активную подписку пользователя досрочно.

        Возвращает отменённую подписку (с оставшимся сроком) или None,
        если активной подписки не было. reason: 'user' | 'admin' | 'expired'.
        """
        conn = await self._conn()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND active = 1 "
                "AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
                (user_id, now()),
            )
            row = await cur.fetchone()
            if not row:
                return None
            await conn.execute(
                "UPDATE subscriptions SET active = 0, cancelled_at = ?, cancel_reason = ? "
                "WHERE id = ?",
                (now(), reason, row["id"]),
            )
            await conn.commit()
            return dict(row)
        finally:
            await conn.close()

    async def expire_due(self) -> int:
        """Помечает просроченные подписки неактивными. Возвращает количество."""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "UPDATE subscriptions SET active = 0, cancelled_at = ?, "
                "cancel_reason = COALESCE(cancel_reason, 'expired') "
                "WHERE active = 1 AND expires_at <= ?",
                (now(), now()),
            )
            await conn.commit()
            return cur.rowcount or 0
        finally:
            await conn.close()

    async def count_active(self) -> int:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM subscriptions "
                "WHERE active = 1 AND expires_at > ?",
                (now(),),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            await conn.close()
