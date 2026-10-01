"""
Ссылка на канал/чат для проверки подписки
"""

import json

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
        await self.set_with_invite(link, "")

    async def set_with_invite(self, link: str, invite_link: str) -> None:
        """Persist channel and its explicit invite link in one transaction."""
        conn = await self._conn()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "UPDATE channel_settings SET link = ?, updated_at = ? WHERE id = 1", (link, now())
            )
            await conn.execute(
                "INSERT INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                ("channel_invite_link", json.dumps({"channel": link, "url": invite_link}), now()),
            )
            await conn.commit()
        finally:
            await conn.close()
