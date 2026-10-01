"""
Платежи Telegram Stars
"""

import aiosqlite

from repositories.base import LIFETIME_EXPIRES, Base
from repositories.subscriptions import SubscriptionRepo


class PaymentRepo(Base):
    async def issue_invoice(
        self,
        user_id: int,
        code: str,
        plan: str,
        days: int,
        stars: int,
        lifetime: bool = False,
    ) -> str:
        """Persist the immutable, user-bound price snapshot before sending to Telegram."""
        from secrets import token_urlsafe

        payload = "sub2|" + token_urlsafe(24)
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO issued_invoices (payload,user_id,code,plan,days,stars,lifetime) "
                "VALUES (?,?,?,?,?,?,?)",
                (payload, user_id, code, plan, days, stars, int(lifetime)),
            )
            await conn.commit()
            return payload
        finally:
            await conn.close()

    async def get_invoice(self, payload: str, user_id: int) -> dict | None:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT code,plan,days,stars,lifetime FROM issued_invoices "
                "WHERE payload = ? AND user_id = ?",
                (payload, user_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def add(self, user_id: int, charge_id: str, plan: str, days: int, stars: int) -> bool:
        """Записывает платёж. False — если charge_id уже обработан (идемпотентность)."""
        conn = await self._conn()
        try:
            try:
                await conn.execute(
                    "INSERT INTO payments (user_id, charge_id, plan, days, stars) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, charge_id, plan, days, stars),
                )
                await conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False
        finally:
            await conn.close()

    async def record_and_activate(
        self,
        user_id: int,
        charge_id: str,
        plan: str,
        days: int,
        stars: int,
        lifetime: bool = False,
    ) -> tuple[bool, str]:
        """Atomically record and fulfill; True means a grant was applied this call.

        Legacy recorded-but-unfulfilled charges are recovered on redelivery.
        The persisted expiry prevents regranting even after cancellation.
        """
        if not charge_id or plan != "pro" or stars <= 0 or (not lifetime and days <= 0):
            raise ValueError("Invalid subscription payment")
        conn = await self._conn()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute("SELECT * FROM payments WHERE charge_id = ?", (charge_id,))
            row = await cur.fetchone()
            if row:
                if (row["user_id"], row["plan"], row["days"], row["stars"]) != (
                    user_id,
                    plan,
                    days,
                    stars,
                ):
                    raise ValueError("Charge does not match the recorded payment")
                if row["applied_expires"]:
                    return False, row["applied_expires"]
                cur = await conn.execute(
                    "SELECT expires_at FROM subscriptions WHERE charge_id = ? "
                    "AND user_id = ? ORDER BY id DESC LIMIT 1",
                    (charge_id, user_id),
                )
                applied = await cur.fetchone()
                if applied:
                    await conn.execute(
                        "UPDATE payments SET applied_expires = ? WHERE charge_id = ?",
                        (applied["expires_at"], charge_id),
                    )
                    await conn.commit()
                    return False, applied["expires_at"]
                # No subscription carries this charge: either the old flow crashed
                # between recording and granting (recover it), or it was a no-op
                # because a lifetime grant was already active when the payment was
                # taken — activate_in_transaction returns early and inserts nothing.
                # Old code no-op'd there too, so no entitlement is owed; regranting
                # after the user later cancels would hand out free service.
                # Strict `<`: a lifetime grant in the SAME second is treated as
                # genuine crash recovery, so a paying user is never silently dropped.
                cur = await conn.execute(
                    "SELECT expires_at FROM subscriptions WHERE user_id = ? "
                    "AND started_at < ? AND expires_at >= ? "
                    "ORDER BY expires_at DESC LIMIT 1",
                    (user_id, row["ts"], LIFETIME_EXPIRES),
                )
                silent = await cur.fetchone()
                if silent:
                    await conn.execute(
                        "UPDATE payments SET applied_expires = ? WHERE charge_id = ?",
                        (silent["expires_at"], charge_id),
                    )
                    await conn.commit()
                    return False, silent["expires_at"]
            else:
                await conn.execute(
                    "INSERT INTO payments (user_id, charge_id, plan, days, stars) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, charge_id, plan, days, stars),
                )
            expires = await SubscriptionRepo.activate_in_transaction(
                conn, user_id, plan, days, "stars", charge_id, lifetime
            )
            await conn.execute(
                "UPDATE payments SET applied_expires = ? WHERE charge_id = ?", (expires, charge_id)
            )
            await conn.commit()
            return True, expires
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def total_stars(self, since: str | None = None) -> int:
        conn = await self._conn()
        try:
            if since:
                cur = await conn.execute(
                    "SELECT COALESCE(SUM(stars), 0) FROM payments WHERE ts >= ?", (since,)
                )
            else:
                cur = await conn.execute("SELECT COALESCE(SUM(stars), 0) FROM payments")
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            await conn.close()

    async def count(self, since: str | None = None) -> int:
        conn = await self._conn()
        try:
            if since:
                cur = await conn.execute("SELECT COUNT(*) FROM payments WHERE ts >= ?", (since,))
            else:
                cur = await conn.execute("SELECT COUNT(*) FROM payments")
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            await conn.close()
