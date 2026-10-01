"""Waiters retain their per-user lock when another user triggers pruning."""
import asyncio

from locks import UserLocks


async def test_pruning_preserves_woken_waiter():
    locks = UserLocks(max_locks=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    overlap = False

    async def waiting():
        nonlocal active, overlap
        async with locks.hold(1):
            active += 1
            overlap |= active > 1
            entered.set()
            await release.wait()
            active -= 1

    async with locks.hold(1):
        waiter = asyncio.create_task(waiting())
        await asyncio.sleep(0)
    async with locks.hold(2):
        await entered.wait()
        async def again():
            nonlocal active, overlap
            async with locks.hold(1):
                active += 1
                overlap |= active > 1
                active -= 1
        another = asyncio.create_task(again())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(waiter, another)
    assert not overlap
    assert not locks._locks
