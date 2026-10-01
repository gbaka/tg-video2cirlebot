"""Обращения в поддержку: журнал, по которому считаются лимиты против спама."""

from repositories.base import Base


class SupportRepo(Base):
    async def cooldown_left(self, user_id: int, cooldown_sec: int) -> int:
        """Сколько секунд осталось до следующего обращения (0 — можно писать)."""
        if cooldown_sec <= 0:
            return 0
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT CAST(strftime('%s','now') - strftime('%s', MAX(ts)) AS INTEGER) "
                "FROM support_requests WHERE user_id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
            elapsed = int(row[0]) if row and row[0] is not None else None
        finally:
            await conn.close()
        if elapsed is None:
            return 0
        return max(0, cooldown_sec - max(0, elapsed))

    async def count_today(self, user_id: int) -> int:
        """Число обращений за сегодня (UTC); предикат sargable по индексу ts."""
        return await self._scalar(
            "SELECT COUNT(*) FROM support_requests "
            "WHERE user_id = ? AND ts >= date('now')",
            (user_id,),
        )

    async def record(self, user_id: int) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO support_requests (user_id) VALUES (?)", (user_id,)
            )
            await conn.commit()
        finally:
            await conn.close()
