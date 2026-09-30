"""
Реестр активных задач и планировщик фоновых задач.

TaskRegistry — что бот делает прямо сейчас (активные конвертации, читается админом).
JobRunner — периодические фоновые задачи (истечение подписок, очистка, агрегация).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class ActiveTask:
    task_id: str
    kind: str            # 'convert'
    user_id: int
    chat_id: int
    filename: str
    size: int
    started_at: float


class TaskRegistry:
    """In-memory реестр активных задач бота."""

    def __init__(self) -> None:
        self._tasks: dict[str, ActiveTask] = {}

    def add(self, kind: str, user_id: int, chat_id: int, filename: str, size: int) -> str:
        task_id = uuid.uuid4().hex[:8]
        self._tasks[task_id] = ActiveTask(
            task_id=task_id, kind=kind, user_id=user_id, chat_id=chat_id,
            filename=filename, size=size, started_at=time.time(),
        )
        return task_id

    def remove(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)

    def all(self) -> list[ActiveTask]:
        # Сначала самые давние
        return sorted(self._tasks.values(), key=lambda t: t.started_at)

    def count(self) -> int:
        return len(self._tasks)

    def elapsed(self, task: ActiveTask) -> int:
        return int(time.time() - task.started_at)

    def count_by_user(self, user_id: int) -> int:
        return sum(1 for t in self._tasks.values() if t.user_id == user_id)


@dataclass
class Job:
    name: str
    func: Callable[[], Awaitable[None]]
    interval: int
    runs: int = 0
    last_run: datetime | None = None
    last_error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


class JobRunner:
    """Запускает периодические фоновые задачи."""

    def __init__(self) -> None:
        self._jobs: list[Job] = []
        self._stopping = False

    def register(self, name: str, func: Callable[[], Awaitable[None]], interval: int) -> None:
        self._jobs.append(Job(name=name, func=func, interval=interval))

    @property
    def jobs(self) -> list[Job]:
        return self._jobs

    async def start(self) -> None:
        self._stopping = False
        for job in self._jobs:
            job.task = asyncio.create_task(self._loop(job), name=f"job:{job.name}")
        logger.info("Запущено фоновых задач: %d", len(self._jobs))

    async def _loop(self, job: Job) -> None:
        # Небольшая задержка, чтобы не бить по БД на старте
        await asyncio.sleep(2)
        while not self._stopping:
            try:
                await job.func()
                job.runs += 1
                job.last_run = datetime.now(UTC)
                job.last_error = None
            except asyncio.CancelledError:
                break
            except Exception as e:  # не роняем планировщик из-за одной задачи
                job.last_error = str(e)
                logger.exception("Ошибка фоновой задачи '%s'", job.name)
            await asyncio.sleep(job.interval)

    async def stop(self) -> None:
        self._stopping = True
        for job in self._jobs:
            if job.task:
                job.task.cancel()
        for job in self._jobs:
            if job.task:
                # Гасим задачу молча: при остановке приложения её падение
                # не должно мешать погасить остальные
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await job.task
