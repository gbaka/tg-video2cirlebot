import asyncio

import pytest


async def test_cancel_before_worker_starts_reclaims_running_slot():
    from conversion_queue import ConversionQueue

    queue = ConversionQueue(queue_size=1)
    called = []

    async def job():
        called.append(1)

    active = queue.submit(job)
    waiting = queue.submit(job)
    active.cancel()
    await queue.shutdown(timeout=0.02)
    assert queue.pending() == 0
    assert not queue._active
    assert waiting.future.done()


async def test_queue_is_bounded_reports_waiting_position_and_drains():
    from conversion_queue import ConversionQueue, QueueFull

    queue = ConversionQueue(workers=1, queue_size=1)
    gate = asyncio.Event()
    running = 0
    peak = 0

    async def job():
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await gate.wait()
        running -= 1
        return 42

    first = queue.submit(job)
    second = queue.submit(job)
    assert first.position == 0
    assert second.position == 1
    with pytest.raises(QueueFull):
        queue.submit(job)
    gate.set()
    assert await first.wait() == await second.wait() == 42
    await queue.shutdown(timeout=1)
    assert peak == 1
    with pytest.raises(RuntimeError):
        queue.submit(job)


async def test_shutdown_deadline_cancels_running_and_waiting_jobs():
    from conversion_queue import ConversionQueue

    queue = ConversionQueue(queue_size=1)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    active = queue.submit(job)
    waiting = queue.submit(job)
    await started.wait()
    await queue.shutdown(timeout=0.02)
    assert stopped.is_set()
    assert active.future.cancelled()
    assert waiting.future.cancelled()
    assert queue.pending() == 0


async def test_cancel_shutdown_cleans_running_and_waiting_jobs():
    from conversion_queue import ConversionQueue
    queue = ConversionQueue(queue_size=1)
    started, stopped = asyncio.Event(), asyncio.Event()
    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    active = queue.submit(job)
    waiting = queue.submit(job)
    await started.wait()
    closing = asyncio.create_task(queue.shutdown(timeout=1))
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert stopped.is_set()
    assert active.future.cancelled() and waiting.future.cancelled()
    assert queue.pending() == 0



async def test_cancel_waiter_reclaims_slot_and_running_cancel_finishes():
    from conversion_queue import ConversionQueue

    queue = ConversionQueue(queue_size=1)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    active = queue.submit(job)
    waiting = queue.submit(job)
    await started.wait()
    waiting.cancel()
    assert queue.pending() == 0
    active.cancel()
    await queue.shutdown(timeout=1)
    assert stopped.is_set()
    assert active.future.cancelled()
