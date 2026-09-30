"""
Статистика конвертаций
"""

from repositories.base import Base
from timeutil import ago


class UsageRepo(Base):
    async def add(
        self, user_id: int | None, status: str, plan: str, file_size: int = 0,
        duration: float = 0.0, fmt: str = "", error: str = "", processing_ms: int = 0
    ) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO usage (user_id, status, plan, file_size, duration, "
                "format, error, processing_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, status, plan, file_size, duration, fmt, error, processing_ms),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def count_since(self, since: str, status: str | None = None) -> int:
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
                "SELECT AVG(processing_ms) FROM usage "
                "WHERE ts >= ? AND status = ? AND processing_ms > 0",
                (since, status),
            )
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            await conn.close()

    async def total_bytes(self, since: str) -> int:
        return await self._scalar(
            "SELECT COALESCE(SUM(file_size), 0) FROM usage "
            "WHERE ts >= ? AND status = 'ok'",
            (since,),
        )

    async def daily_counts(self, days: int = 7) -> list[tuple[str, int]]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT date(ts) d, COUNT(*) c FROM usage WHERE ts >= ? GROUP BY d ORDER BY d",
                (ago(days=days),),
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
                "  COALESCE(SUM(status = 'ok' AND ts >= date('now')), 0) AS today, "
                "  MAX(ts) AS last_ts "
                "FROM usage WHERE user_id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
            if row is None:  # без GROUP BY запрос всегда даёт строку
                return {"ok": 0, "errors": 0, "today": 0, "last_usage": None}
            return {
                "ok": int(row["ok"] or 0),
                "errors": int(row["err"] or 0),
                "today": int(row["today"] or 0),
                "last_usage": row["last_ts"],
            }
        finally:
            await conn.close()
