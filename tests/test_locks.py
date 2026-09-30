"""Пер-пользовательские блокировки: сериализация, отсутствие утечки."""

import asyncio

from locks import UserLocks


async def test_same_user_is_serialized() -> None:
    locks = UserLocks()
    order: list[str] = []

    async def job(tag: str) -> None:
        async with locks.hold(1):
            order.append(f"{tag}-start")
            await asyncio.sleep(0.05)
            order.append(f"{tag}-end")

    await asyncio.gather(job("a"), job("b"), job("c"))
    assert order == ["a-start", "a-end", "b-start", "b-end", "c-start", "c-end"]


async def test_different_users_run_in_parallel() -> None:
    locks = UserLocks()
    order: list[str] = []

    async def job(uid: int) -> None:
        async with locks.hold(uid):
            order.append(f"{uid}-start")
            await asyncio.sleep(0.05)
            order.append(f"{uid}-end")

    await asyncio.gather(job(1), job(2))
    # оба вошли до того, как кто-то вышел
    assert order[:2] == ["1-start", "2-start"]


async def test_locks_are_released_after_use() -> None:
    locks = UserLocks()
    async with locks.hold(42):
        assert locks.busy() == 1
    assert locks.busy() == 0
    assert locks._locks == {}          # словарь не копит мёртвые блокировки


async def test_lock_released_on_exception() -> None:
    locks = UserLocks()
    try:
        async with locks.hold(7):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert locks.busy() == 0
    assert locks._locks == {}


async def test_many_users_do_not_grow_the_dict() -> None:
    locks = UserLocks(max_locks=10)
    for uid in range(50):
        async with locks.hold(uid):
            pass
    assert len(locks._locks) <= 10
