"""
Репозитории: доступ к данным SQLite (пользователи, подписки, статистика, настройки).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import aiosqlite

from db import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _ago(days: int = 0, hours: int = 0, minutes: int = 0) -> str:
    return (datetime.utcnow() - timedelta(days=days, hours=hours, minutes=minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _future(days: int = 0, hours: int = 0) -> str:
    return (datetime.utcnow() + timedelta(days=days, hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# Бессрочная («вечная») подписка — дата в далёком будущем
LIFETIME_EXPIRES = "9999-12-31 23:59:59"


def is_lifetime(expires_at: Optional[str]) -> bool:
    """True, если подписка бессрочная."""
    return bool(expires_at) and expires_at >= LIFETIME_EXPIRES[:10]


class _Base:
    def __init__(self, db: Database):
        self.db = db

    async def _conn(self) -> aiosqlite.Connection:
        conn = await self.db.connect()
        conn.row_factory = aiosqlite.Row
        return conn


class UserRepo(_Base):
    async def get_or_create(
        self, user_id: int, username: Optional[str], first_name: Optional[str],
        default_language: str = "ru"
    ) -> dict:
        conn = await self._conn()
        try:
            cur = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            if row is None:
                await conn.execute(
                    "INSERT INTO users (user_id, username, first_name, language) VALUES (?, ?, ?, ?)",
                    (user_id, username, first_name, default_language),
                )
                await conn.commit()
                cur = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = await cur.fetchone()
            else:
                await conn.execute(
                    "UPDATE users SET username = ?, first_name = ?, last_seen = ? WHERE user_id = ?",
                    (username, first_name, _now(), user_id),
                )
                await conn.commit()
            return dict(row)
        finally:
            await conn.close()

    async def get(self, user_id: int) -> Optional[dict]:
        conn = await self._conn()
        try:
            cur = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def set_language(self, user_id: int, lang: str) -> None:
        await self._set(user_id, "language", lang)

    async def set_quality(self, user_id: int, quality: Optional[int]) -> None:
        await self._set(user_id, "quality", quality)

    async def _set(self, user_id: int, field: str, value: Any) -> None:
        conn = await self._conn()
        try:
            await conn.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
            await conn.commit()
        finally:
            await conn.close()

    async def count_all(self) -> int:
        return await self._scalar("SELECT COUNT(*) FROM users")

    async def count_since(self, since: str) -> int:
        return await self._scalar("SELECT COUNT(*) FROM users WHERE created_at >= ?", (since,))

    async def count_active_since(self, since: str) -> int:
        return await self._scalar("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (since,))

    async def list_recent(self, limit: int = 20) -> list[dict]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    @staticmethod
    def _build_filter(query: str) -> tuple[str, list]:
        """Фильтр для поиска: точный ID или LIKE по username/имени."""
        q = (query or "").strip().lstrip("@")
        if not q:
            return "", []
        if q.isdigit():
            return "WHERE u.user_id = ?", [int(q)]
        like = f"%{q}%"
        return "WHERE u.username LIKE ? OR u.first_name LIKE ?", [like, like]

    async def search_page(
        self, offset: int, limit: int, query: str = ""
    ) -> tuple[list[dict], int]:
        """
        Страница пользователей с активным тарифом, без N+1.

        Returns:
            (строки с полем active_plan, общее количество по фильтру)
        """
        where, params = self._build_filter(query)

        conn = await self._conn()
        try:
            cur = await conn.execute(f"SELECT COUNT(*) FROM users u {where}", tuple(params))
            row = await cur.fetchone()
            total = int(row[0]) if row else 0

            # Подзапрос отдаёт активный тариф одним запросом (вместо запроса на каждого)
            sql = f"""
                SELECT u.*, (
                    SELECT s.plan FROM subscriptions s
                    WHERE s.user_id = u.user_id AND s.active = 1 AND s.expires_at > ?
                    ORDER BY s.expires_at DESC LIMIT 1
                ) AS active_plan
                FROM users u {where}
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
            """
            cur = await conn.execute(sql, tuple([_now()] + params + [limit, offset]))
            return [dict(r) for r in await cur.fetchall()], total
        finally:
            await conn.close()

    async def export_page(self, offset: int, limit: int) -> list[dict]:
        """Страница пользователей с тарифом и счётчиками — для выгрузки в CSV."""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                """
                SELECT u.user_id, u.username, u.first_name, u.language, u.quality,
                       u.created_at, u.last_seen,
                       (SELECT s.plan FROM subscriptions s
                         WHERE s.user_id = u.user_id AND s.active = 1 AND s.expires_at > ?
                         ORDER BY s.expires_at DESC LIMIT 1) AS active_plan,
                       (SELECT s.expires_at FROM subscriptions s
                         WHERE s.user_id = u.user_id AND s.active = 1 AND s.expires_at > ?
                         ORDER BY s.expires_at DESC LIMIT 1) AS expires_at,
                       (SELECT COUNT(*) FROM usage x
                         WHERE x.user_id = u.user_id AND x.status = 'ok') AS conv_ok,
                       (SELECT COUNT(*) FROM usage x
                         WHERE x.user_id = u.user_id AND x.status = 'error') AS conv_err
                FROM users u
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (_now(), _now(), limit, offset),
            )
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def _scalar(self, sql: str, params: tuple = ()) -> int:
        conn = await self._conn()
        try:
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            await conn.close()


class SubscriptionRepo(_Base):
    async def deactivate(self, user_id: int) -> int:
        """Снимает активные подписки пользователя. Возвращает число снятых."""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "UPDATE subscriptions SET active = 0 WHERE user_id = ? AND active = 1",
                (user_id,),
            )
            await conn.commit()
            return cur.rowcount or 0
        finally:
            await conn.close()

    async def get_active(self, user_id: int) -> Optional[dict]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND active = 1 "
                "AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
                (user_id, _now()),
            )
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def activate(
        self, user_id: int, plan: str, days: int, source: str,
        charge_id: Optional[str] = None, lifetime: bool = False,
    ) -> str:
        """Активирует подписку. Если активна — продлевает от текущей даты окончания."""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT expires_at FROM subscriptions WHERE user_id = ? AND active = 1 "
                "AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
                (user_id, _now()),
            )
            row = await cur.fetchone()
            base = None
            if row:
                # Бессрочную подписку не продлеваем — она уже максимальная
                if is_lifetime(row["expires_at"]):
                    return row["expires_at"]
                base = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
                await conn.execute(
                    "UPDATE subscriptions SET active = 0 WHERE user_id = ? AND active = 1",
                    (user_id,),
                )
            if base is None:
                base = datetime.utcnow()
            expires = LIFETIME_EXPIRES if lifetime else (
                base + timedelta(days=days)
            ).strftime("%Y-%m-%d %H:%M:%S")
            await conn.execute(
                "INSERT INTO subscriptions (user_id, plan, expires_at, source, charge_id, active) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (user_id, plan, expires, source, charge_id),
            )
            await conn.commit()
            return expires
        finally:
            await conn.close()

    async def cancel(self, user_id: int, reason: str = "user") -> dict | None:
        """
        Отменяет активную подписку пользователя досрочно.

        Возвращает отменённую подписку (с оставшимся сроком) или None,
        если активной подписки не было. reason: 'user' | 'admin' | 'expired'.
        """
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND active = 1 "
                "AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
                (user_id, _now()),
            )
            row = await cur.fetchone()
            if not row:
                return None
            await conn.execute(
                "UPDATE subscriptions SET active = 0, cancelled_at = ?, cancel_reason = ? "
                "WHERE id = ?",
                (_now(), reason, row["id"]),
            )
            await conn.commit()
            return dict(row)
        finally:
            await conn.close()

    async def deactivate(self, user_id: int) -> None:
        """Внутреннее снятие флага активности без причины (служебное)."""
        conn = await self._conn()
        try:
            await conn.execute(
                "UPDATE subscriptions SET active = 0 WHERE user_id = ? AND active = 1", (user_id,)
            )
            await conn.commit()
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
                (_now(), _now()),
            )
            await conn.commit()
            return cur.rowcount or 0
        finally:
            await conn.close()

    async def count_active(self) -> int:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE active = 1 AND expires_at > ?",
                (_now(),),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            await conn.close()


class UsageRepo(_Base):
    async def add(
        self, user_id: Optional[int], status: str, plan: str, file_size: int = 0,
        duration: float = 0.0, fmt: str = "", error: str = "", processing_ms: int = 0
    ) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO usage (user_id, status, plan, file_size, duration, format, error, processing_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, status, plan, file_size, duration, fmt, error, processing_ms),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def count_since(self, since: str, status: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM usage WHERE ts >= ?"
        params: list = [since]
        if status:
            sql += " AND status = ?"
            params.append(status)
        return await self._scalar(sql, tuple(params))

    async def count_all(self) -> int:
        return await self._scalar("SELECT COUNT(*) FROM usage")

    async def count_user_today(self, user_id: int) -> int:
        """
        Успешные конвертации пользователя за текущие сутки (UTC).

        `ts >= date('now')` вместо `date(ts) = date('now')`: дата хранится
        в UTC (CURRENT_TIMESTAMP), поэтому сравнение по строке эквивалентно,
        но остаётся 'sargable' — запрос использует индекс (user_id, ts).
        """
        return await self._scalar(
            "SELECT COUNT(*) FROM usage "
            "WHERE user_id = ? AND status = 'ok' AND ts >= date('now')",
            (user_id,),
        )

    async def top_formats(self, since: str, limit: int = 5) -> list[tuple[str, int]]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT format, COUNT(*) c FROM usage WHERE ts >= ? AND format != '' "
                "GROUP BY format ORDER BY c DESC LIMIT ?",
                (since, limit),
            )
            return [(r[0], int(r[1])) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def avg_processing_ms(self, since: str, status: str = "ok") -> int:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT AVG(processing_ms) FROM usage WHERE ts >= ? AND status = ? AND processing_ms > 0",
                (since, status),
            )
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            await conn.close()

    async def total_bytes(self, since: str) -> int:
        return await self._scalar(
            "SELECT COALESCE(SUM(file_size), 0) FROM usage WHERE ts >= ? AND status = 'ok'", (since,)
        )

    async def daily_counts(self, days: int = 7) -> list[tuple[str, int]]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT date(ts) d, COUNT(*) c FROM usage WHERE ts >= ? GROUP BY d ORDER BY d",
                (_ago(days=days),),
            )
            return [(r[0], int(r[1])) for r in await cur.fetchall()]
        finally:
            await conn.close()

    async def active_users_since(self, since: str) -> int:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM usage WHERE ts >= ?", (since,)
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            await conn.close()

    async def stats_for_user(self, user_id: int) -> dict:
        """Сводка по пользователю для карточки."""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT "
                "  COALESCE(SUM(status = 'ok'), 0) AS ok, "
                "  COALESCE(SUM(status = 'error'), 0) AS err, "
                "  COALESCE(SUM(status = 'ok' AND date(ts) = date('now')), 0) AS today, "
                "  MAX(ts) AS last_ts "
                "FROM usage WHERE user_id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
            return {
                "ok": int(row["ok"] or 0),
                "errors": int(row["err"] or 0),
                "today": int(row["today"] or 0),
                "last_usage": row["last_ts"],
            }
        finally:
            await conn.close()

    async def _scalar(self, sql: str, params: tuple = ()) -> int:
        conn = await self._conn()
        try:
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            await conn.close()


class SettingsRepo(_Base):
    async def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        conn = await self._conn()
        try:
            cur = await conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
            row = await cur.fetchone()
            return row[0] if row else default
        finally:
            await conn.close()

    async def set(self, key: str, value: str) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, _now()),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_bool(self, key: str, default: bool = False) -> bool:
        val = await self.get(key)
        if val is None:
            return default
        return val.lower() in ("1", "true", "yes", "on")

    async def get_int(self, key: str, default: int = 0) -> int:
        val = await self.get(key)
        try:
            return int(val) if val is not None else default
        except ValueError:
            return default

    async def all(self) -> dict[str, str]:
        conn = await self._conn()
        try:
            cur = await conn.execute("SELECT key, value FROM bot_settings")
            return {r[0]: r[1] for r in await cur.fetchall()}
        finally:
            await conn.close()


class ChannelRepo(_Base):
    async def get_link(self) -> str:
        conn = await self._conn()
        try:
            cur = await conn.execute("SELECT link FROM channel_settings WHERE id = 1")
            row = await cur.fetchone()
            return row[0] if row else ""
        finally:
            await conn.close()

    async def set_link(self, link: str) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "UPDATE channel_settings SET link = ?, updated_at = ? WHERE id = 1", (link, _now())
            )
            await conn.commit()
        finally:
            await conn.close()


class PaymentRepo(_Base):
    async def add(
        self, user_id: int, charge_id: str, plan: str, days: int, stars: int
    ) -> bool:
        """Записывает платёж. False — если charge_id уже обработан (идемпотентность)."""
        conn = await self._conn()
        try:
            try:
                await conn.execute(
                    "INSERT INTO payments (user_id, charge_id, plan, days, stars) VALUES (?, ?, ?, ?, ?)",
                    (user_id, charge_id, plan, days, stars),
                )
                await conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False
        finally:
            await conn.close()

    async def total_stars(self, since: Optional[str] = None) -> int:
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

    async def count(self, since: Optional[str] = None) -> int:
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
