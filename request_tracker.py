"""Track and drain actual aiogram update tasks before closing network resources."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class RequestTracker(BaseMiddleware):
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._accepting = True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not self._accepting:
            return None
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            return await handler(event, data)
        finally:
            if task is not None:
                self._tasks.discard(task)

    def close(self) -> None:
        self._accepting = False

    def count(self) -> int:
        return len(self._tasks)

    async def shutdown(self, timeout: float = 30) -> None:
        self.close()
        tasks = self._tasks - {asyncio.current_task()}
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=max(0, timeout))
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
