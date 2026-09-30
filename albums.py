"""Буферизация альбомов (media groups).

Telegram доставляет альбом как N отдельных сообщений с одинаковым
`media_group_id`. Чтобы применить лимит «видео в одном сообщении» и
обработать пачку одним заходом, собираем сообщения и запускаем обработку
через `delay` секунд после последнего сообщения группы.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

ALBUM_MAX = 10  # максимальный размер альбома в Telegram


class AlbumBuffer:
    """Собирает сообщения одного альбома и отдаёт их одним списком."""

    def __init__(self, delay: float = 1.2, max_size: int = ALBUM_MAX) -> None:
        self._delay = delay
        self._max_size = max_size
        self._groups: dict[str, list] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def add(
        self,
        group_id: str,
        message,
        handler: Callable[[list], Awaitable[None]],
    ) -> None:
        """Добавляет сообщение в группу и (пере)планирует обработку."""
        bucket = self._groups.setdefault(group_id, [])
        if len(bucket) < self._max_size:
            bucket.append(message)

        previous = self._tasks.get(group_id)
        if previous and not previous.done():
            previous.cancel()  # дебаунс: ждём тишины в группе
        self._tasks[group_id] = asyncio.create_task(self._flush(group_id, handler))

    async def _flush(self, group_id: str, handler: Callable[[list], Awaitable[None]]) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return

        messages = self._groups.pop(group_id, [])
        self._tasks.pop(group_id, None)
        if not messages:
            return
        # Стабильный порядок: как их пронумеровал Telegram
        messages.sort(key=lambda m: m.message_id)
        try:
            await handler(messages)
        except Exception:
            logger.exception("Ошибка обработки альбома %s", group_id)

    def pending(self) -> int:
        """Сколько сообщений ждёт обработки (для админ-панели задач)."""
        return sum(len(v) for v in self._groups.values())

    def cancel_all(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self._groups.clear()
