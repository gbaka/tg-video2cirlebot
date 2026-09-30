"""Заглушки Telegram-объектов: позволяют тестировать хендлеры без сети и ffmpeg."""

from types import SimpleNamespace
from typing import Any


class FakeStatus:
    """Сообщение-статус: помнит правки и факт удаления."""

    def __init__(self) -> None:
        self.edits: list[str] = []
        self.kwargs: list[dict[str, Any]] = []
        self.deleted = False

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append(text)
        self.kwargs.append(kwargs)

    async def delete(self) -> None:
        self.deleted = True


class FakeMessage:
    """Сообщение пользователя с видео."""

    def __init__(self, fid: int = 1, size: int = 1_000_000, name: str | None = None) -> None:
        self.message_id = fid
        self.chat = SimpleNamespace(id=1000 + fid)
        self.video = SimpleNamespace(
            file_id=f"file{fid}",
            file_name=name or f"video{fid}.mp4",
            file_size=size,
            file_unique_id=str(fid),
        )
        self.video_note = None
        self.document = None
        self.answers: list[str] = []
        self.answer_kwargs: list[dict[str, Any]] = []
        self.statuses: list[FakeStatus] = []

    async def answer(self, text: str, **kwargs: Any) -> FakeStatus:
        self.answers.append(text)
        self.answer_kwargs.append(kwargs)
        status = FakeStatus()
        self.statuses.append(status)
        return status

    def lifecycle_touched(self) -> bool:
        """Правки или удаление сообщений — признак жизненного цикла статуса."""
        return any(st.edits or st.deleted for st in self.statuses)


class FakeUsage:
    """Счётчик успешных конвертаций за сутки (как реальный запрос к БД)."""

    def __init__(self, used: int = 0) -> None:
        self.rows = used

    async def count_user_today(self, user_id: int) -> int:
        return self.rows


class FakeContext:
    """Минимальный AppContext для проверки логики лимитов."""

    def __init__(self, used: int = 0, locks: Any = None) -> None:
        from locks import UserLocks

        self.usage = FakeUsage(used)
        self.locks = locks if locks is not None else UserLocks()
        self.tasks = SimpleNamespace(add=lambda **kw: "task1", remove=lambda task_id: None)
        self.config = SimpleNamespace(bot=SimpleNamespace(admin_ids=[999]))
