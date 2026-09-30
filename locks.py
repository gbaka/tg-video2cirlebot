"""Пер-пользовательские блокировки.

aiogram обрабатывает апдейты параллельно, поэтому два видео, отправленных
подряд (не альбомом), могли одновременно пройти проверку дневного лимита и
превысить его. Блокировка сериализует обработку видео одного пользователя.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class UserLocks:
    """Выдаёт эксклюзивную блокировку на пользователя и не течёт по памяти."""

    def __init__(self, max_locks: int = 2000) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._refs: dict[int, int] = {}
        self._max = max_locks

    @asynccontextmanager
    async def hold(self, user_id: int):
        lock = self._locks.get(user_id)
        if lock is None:
            if len(self._locks) >= self._max:
                # Страховка от неограниченного роста: освобождаем простаивающие
                for uid in [u for u, l in self._locks.items() if not l.locked()]:
                    self._locks.pop(uid, None)
                    self._refs.pop(uid, None)
            lock = self._locks[user_id] = asyncio.Lock()

        self._refs[user_id] = self._refs.get(user_id, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._refs[user_id] -= 1
            if self._refs[user_id] <= 0:
                self._refs.pop(user_id, None)
                self._locks.pop(user_id, None)

    def busy(self) -> int:
        """Сколько пользователей сейчас что-то конвертируют."""
        return sum(1 for lock in self._locks.values() if lock.locked())
