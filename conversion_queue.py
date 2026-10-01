"""Bounded FIFO conversion admission; submitted callables run only in a worker slot."""

import asyncio
import contextlib
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any


class QueueFull(Exception):
    """All worker and waiting slots are occupied."""


class QueueJob:
    def __init__(self, owner: "ConversionQueue", factory: Callable[[], Awaitable[Any]]):
        self.owner = owner
        self.factory = factory
        self.future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.task: asyncio.Task[None] | None = None

    @property
    def position(self) -> int:
        try:
            return self.owner._waiting.index(self) + 1
        except ValueError:
            return 0

    def cancel(self) -> None:
        self.future.cancel()
        if self.task:
            self.task.cancel()
        else:
            with contextlib.suppress(ValueError):
                self.owner._waiting.remove(self)

    async def wait(self) -> Any:
        try:
            return await asyncio.shield(self.future)
        except asyncio.CancelledError:
            self.cancel()
            if self.task:
                await asyncio.gather(self.task, return_exceptions=True)
            raise


class ConversionQueue:
    def __init__(self, workers: int = 1, queue_size: int = 20):
        if workers < 1 or queue_size < 0:
            raise ValueError("invalid queue capacity")
        self.workers = workers
        self.queue_size = queue_size
        self._waiting: deque[QueueJob] = deque()
        self._active: set[QueueJob] = set()
        self._closed = False

    def submit(self, factory: Callable[[], Awaitable[Any]]) -> QueueJob:
        if self._closed:
            raise RuntimeError("conversion queue is closed")
        if len(self._active) >= self.workers and len(self._waiting) >= self.queue_size:
            raise QueueFull
        job = QueueJob(self, factory)
        if len(self._active) < self.workers:
            self._start(job)
        else:
            self._waiting.append(job)
        return job

    def _start(self, job: QueueJob) -> None:
        self._active.add(job)
        job.task = asyncio.create_task(self._run(job), name="conversion-worker")
        job.task.add_done_callback(lambda _task: self._finished(job))

    def _finished(self, job: QueueJob) -> None:
        if job not in self._active:
            return
        self._active.remove(job)
        if self._waiting:
            self._start(self._waiting.popleft())

    async def _run(self, job: QueueJob) -> None:
        try:
            result = await job.factory()
            if not job.future.done():
                job.future.set_result(result)
        except asyncio.CancelledError:
            job.future.cancel()
            raise
        except Exception as exc:
            if not job.future.done():
                job.future.set_exception(exc)
        finally:
            self._finished(job)

    def pending(self) -> int:
        return len(self._waiting)

    async def shutdown(self, timeout: float = 30) -> None:
        self._closed = True

        async def drain() -> None:
            while self._active:
                await asyncio.sleep(0)
                await asyncio.gather(
                    *(j.task for j in tuple(self._active) if j.task), return_exceptions=True
                )

        draining = asyncio.create_task(drain())
        try:
            await asyncio.wait_for(asyncio.shield(draining), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            for job in tuple(self._waiting):
                job.cancel()
            for job in tuple(self._active):
                job.cancel()
            await draining
            if isinstance(exc, asyncio.CancelledError):
                raise
