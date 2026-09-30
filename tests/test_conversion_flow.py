"""Обработка видео: лимиты, регресс исчезающего сообщения, сериализация."""

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest
from aiogram.types import InlineKeyboardMarkup

from context import AppContext
from plans import Plan
from services import conversion
from tests.fakes import FakeContext, FakeMessage

USER: dict[str, Any] = {"user_id": 7, "language": "ru"}


def free_plan(limit: int = 5, album: int = 3) -> Plan:
    """Тариф Free с настраиваемыми лимитами."""
    return Plan("free", 50, 60, [360], 360, 24, "fast", limit, album)


def run(
    fake: FakeContext,
    plan: Plan,
    messages: Sequence[Any],
    menu: Callable[[], InlineKeyboardMarkup] | None = None,
) -> Any:
    """Вызывает сервис обработки пачки с заглушкой вместо AppContext."""
    return conversion.process_batch(
        cast(AppContext, fake), USER, "ru", plan, list(messages), menu
    )


# ===== Лимит «видео в одном сообщении» =====


async def test_album_over_limit_is_rejected_upfront() -> None:
    album = [FakeMessage(i) for i in range(4)]
    await run(FakeContext(), free_plan(album=3), album)
    assert album[0].answers, "нет отказа"
    assert "3" in album[0].answers[0]
    assert not album[0].lifecycle_touched(), "статус не должен создаваться"


async def test_album_at_limit_is_allowed_past_the_check() -> None:
    """Ровно 3 видео на Free — не отказ; дальше срабатывает уже суточный лимит."""
    album = [FakeMessage(i) for i in range(3)]
    await run(FakeContext(used=5), free_plan(album=3), album)

    texts = album[0].answers + [e for st in album[0].statuses for e in st.edits]
    assert not any("видео в одном сообщении" in a for a in texts), texts
    assert any("5" in a for a in texts), "должен сработать суточный лимит"


async def test_pro_plan_has_no_album_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """На Pro ограничение «видео в одном сообщении» не срабатывает."""
    processed: list[str] = []

    async def stub(
        app_ctx: Any,
        user: Any,
        lang: str,
        plan: Plan,
        item: Any,
        status: Any,
        index: int,
        total: int,
    ) -> bool:
        processed.append(item[2])   # item = (msg, file_id, file_name, file_size)
        return True

    monkeypatch.setattr(conversion, "convert_one", stub)
    pro = Plan("pro", 200, 60, [360, 480, 640], 480, 20, "medium", 0, 0)
    album = [FakeMessage(i) for i in range(10)]

    await run(FakeContext(used=0), pro, album)

    assert not any("видео в одном сообщении" in a for a in album[0].answers)
    assert len(processed) == 10, f"обработано {len(processed)} из 10"


# ===== Дневной лимит =====


async def test_limit_notice_stays_visible() -> None:
    """
    Регресс: сообщение о достигнутом дневном лимите не должно удаляться.

    Было: текст лимита писался в статус-сообщение, а блок finally это же
    сообщение удалял — пользователь видел вспышку и пустоту.
    """
    msg = FakeMessage(1)
    await run(FakeContext(used=5), free_plan(limit=5), [msg])

    assert len(msg.statuses) == 1, "статус-сообщение не создавалось"
    status = msg.statuses[0]
    assert not status.deleted, "сообщение о лимите было удалено (баг вернулся!)"
    assert len(status.edits) == 1
    assert "5" in status.edits[0]


async def test_limit_notice_has_menu_button() -> None:
    """К сообщению о лимите приложено главное меню, чтобы было куда нажать."""
    msg = FakeMessage(1)
    markup = InlineKeyboardMarkup(inline_keyboard=[])
    await run(FakeContext(used=5), free_plan(limit=5), [msg], menu=lambda: markup)

    assert msg.statuses and msg.statuses[0].kwargs, "reply_markup не передан"
    assert msg.statuses[0].kwargs[0]["reply_markup"] is markup


async def test_oversized_file_is_rejected_without_status() -> None:
    msg = FakeMessage(1, size=999 * 1024 * 1024)
    await run(FakeContext(), free_plan(), [msg])
    assert msg.answers and "50" in msg.answers[0]
    assert not msg.lifecycle_touched()


async def test_unsupported_format_is_rejected() -> None:
    msg = FakeMessage(1, name="weird.qqq")
    await run(FakeContext(), free_plan(), [msg])
    assert msg.answers
    assert not msg.lifecycle_touched()


async def test_problems_are_reported_in_one_message() -> None:
    album = [FakeMessage(1, size=999 * 1024 * 1024), FakeMessage(2, name="bad.qqq")]
    await run(FakeContext(), free_plan(), album)
    assert len(album[0].answers) == 1
    assert "50" in album[0].answers[0] and ".qqq" in album[0].answers[0]


# ===== Сериализация =====


async def test_same_user_is_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Гонка на дневном лимите: без блокировки параллельные видео превышали лимит.

    Замер: лимит 5, уже 4 использовано, 4 параллельных видео -> 8 конвертаций.
    """
    fake = FakeContext(used=4)
    started: list[str] = []

    async def counting(*args: Any, **kwargs: Any) -> bool:
        started.append("x")
        await asyncio.sleep(0.05)
        fake.usage.rows += 1        # как реальная запись usage после успеха
        return True

    monkeypatch.setattr(conversion, "convert_one", counting)
    await asyncio.gather(*(
        run(fake, free_plan(limit=5), [FakeMessage(i)]) for i in range(4)
    ))

    assert len(started) == 1, f"лимит превышен: {len(started)} конвертаций при 1 доступной"
    assert fake.usage.rows == 5


async def test_lock_is_released_after_batch() -> None:
    fake = FakeContext(used=5)
    await run(fake, free_plan(limit=5), [FakeMessage(1)])
    assert fake.locks.busy() == 0
    assert fake.locks._locks == {}
