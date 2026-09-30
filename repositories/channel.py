"""
Ссылка на канал/чат для проверки подписки
"""

from repositories.base import Base
from timeutil import now


class ChannelRepo(Base):
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
                "UPDATE channel_settings SET link = ?, updated_at = ? WHERE id = 1", (link, now())
            )
            await conn.commit()
        finally:
            await conn.close()
