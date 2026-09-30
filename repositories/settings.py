"""
Настройки бота (ключ-значение)
"""

from repositories.base import Base
from timeutil import now


class SettingsRepo(Base):
    async def get(self, key: str, default: str | None = None) -> str | None:
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
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, now()),
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
