"""
Общее для репозиториев: подключение, скалярные запросы и понятие
бессрочной подписки.
"""

import logging

import aiosqlite

from db import Database

logger = logging.getLogger(__name__)

#: Бессрочная («вечная») подписка — дата в далёком будущем
LIFETIME_EXPIRES = "9999-12-31 23:59:59"


def is_lifetime(expires_at: str | None) -> bool:
    """True, если подписка бессрочная."""
    if not expires_at:
        return False
    return expires_at[:10] >= LIFETIME_EXPIRES[:10]


class Base:
    """Базовый репозиторий: соединение с нужными настройками."""

    def __init__(self, db: Database):
        self.db = db

    async def _conn(self) -> aiosqlite.Connection:
        conn = await self.db.connect()
        conn.row_factory = aiosqlite.Row
        return conn

    async def _scalar(self, sql: str, params: tuple = ()) -> int:
        conn = await self._conn()
        try:
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            await conn.close()
