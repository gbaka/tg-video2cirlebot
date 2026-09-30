"""Буфер альбомов: дебаунс, порядок сообщений, переполнение."""

import asyncio

import pytest

from albums import ALBUM_MAX, AlbumBuffer


class Msg:
    def __init__(self, mid: int) -> None:
        self.message_id = mid


async def _collect(buf: AlbumBuffer, group: str, ids: list[int], collected: list) -> None:
    async def handler(messages) -> None:
        collected.append([m.message_id for m in messages])

    for mid in ids:
        buf.add(group, Msg(mid), handler)


async def test_messages_are_grouped_and_sorted() -> None:
    buf = AlbumBuffer(delay=0.05)
    seen: list[list[int]] = []
    await _collect(buf, "g1", [3, 1, 2], seen)
    assert buf.pending() == 3
    await asyncio.sleep(0.2)
    assert seen == [[1, 2, 3]]          # порядок как в Telegram, не как пришло
    assert buf.pending() == 0


async def test_debounce_keeps_extending_window() -> None:
    """Сообщения, приходящие с паузой меньше delay, попадают в одну пачку."""
    buf = AlbumBuffer(delay=0.1)
    seen: list[list[int]] = []

    async def handler(messages) -> None:
        seen.append([m.message_id for m in messages])

    buf.add("g", Msg(1), handler)
    await asyncio.sleep(0.05)
    buf.add("g", Msg(2), handler)
    await asyncio.sleep(0.05)
    buf.add("g", Msg(3), handler)
    await asyncio.sleep(0.3)
    assert seen == [[1, 2, 3]]


async def test_group_is_capped() -> None:
    buf = AlbumBuffer(delay=0.05)
    seen: list[list[int]] = []
    await _collect(buf, "g", list(range(20)), seen)
    await asyncio.sleep(0.2)
    assert len(seen[0]) == ALBUM_MAX


async def test_groups_are_independent() -> None:
    buf = AlbumBuffer(delay=0.05)
    seen: list[list[int]] = []

    async def handler(messages) -> None:
        seen.append([m.message_id for m in messages])

    buf.add("a", Msg(1), handler)
    buf.add("b", Msg(2), handler)
    await asyncio.sleep(0.2)
    assert sorted(seen) == [[1], [2]]


async def test_handler_exception_does_not_break_buffer() -> None:
    buf = AlbumBuffer(delay=0.05)
    calls: list[str] = []

    async def boom(messages) -> None:
        calls.append("boom")
        raise RuntimeError("handler failed")

    buf.add("g", Msg(1), boom)
    await asyncio.sleep(0.2)

    seen: list[list[int]] = []

    async def ok(messages) -> None:
        seen.append([m.message_id for m in messages])

    buf.add("h", Msg(9), ok)
    await asyncio.sleep(0.2)
    assert calls == ["boom"]
    assert seen == [[9]]
    assert buf.pending() == 0


async def test_cancel_all_drops_pending() -> None:
    buf = AlbumBuffer(delay=1.0)
    seen: list[list[int]] = []

    async def handler(messages) -> None:
        seen.append([m.message_id for m in messages])

    buf.add("g", Msg(1), handler)
    assert buf.pending() == 1
    buf.cancel_all()
    await asyncio.sleep(0.05)
    assert buf.pending() == 0
    assert seen == []


@pytest.mark.parametrize("delay", [0.0, 0.01])
async def test_tiny_delay_still_works(delay: float) -> None:
    buf = AlbumBuffer(delay=delay)
    seen: list[list[int]] = []
    await _collect(buf, "g", [1, 2], seen)
    await asyncio.sleep(0.1)
    assert seen == [[1, 2]]
