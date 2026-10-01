"""Bounded draining of aiogram update handlers before closing the bot session."""
import asyncio
import importlib
from unittest.mock import AsyncMock


async def test_closed_tracker_rejects_new_work():
    tracker = importlib.import_module("request_tracker").RequestTracker()
    handler = AsyncMock()
    tracker.close()
    await tracker(handler, object(), {})
    handler.assert_not_awaited()


async def test_shutdown_waits_for_accepted_handler():
    tracker = importlib.import_module("request_tracker").RequestTracker()
    started, release = asyncio.Event(), asyncio.Event()
    async def handler(event, data):
        started.set()
        await release.wait()
        return "ok"
    request = asyncio.create_task(tracker(handler, object(), {}))
    await started.wait()
    drain = asyncio.create_task(tracker.shutdown(timeout=1))
    await asyncio.sleep(0)
    assert not drain.done()
    release.set()
    assert await request == "ok"
    await drain
    assert tracker.count() == 0


async def test_shutdown_cancels_overdue_handler_and_awaits_cleanup():
    tracker = importlib.import_module("request_tracker").RequestTracker()
    started, cleaned = asyncio.Event(), asyncio.Event()
    async def handler(event, data):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    request = asyncio.create_task(tracker(handler, object(), {}))
    await started.wait()
    await tracker.shutdown(timeout=0.01)
    assert request.cancelled()
    assert cleaned.is_set()
    assert tracker.count() == 0
