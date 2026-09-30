"""
Платежи Telegram Stars
"""

import aiosqlite

from repositories.base import Base


class PaymentRepo(Base):
    async def add(
        self, user_id: int, charge_id: str, plan: str, days: int, stars: int
    ) -> bool:
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
