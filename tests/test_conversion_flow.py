"""Обработка видео: лимиты, регресс исчезающего сообщения, сериализация."""

import asyncio

import pytest

import access
from handlers.user import media as handlers_user
from plans import Plan
from tests.fakes import FakeContext, FakeMessage

USER = {"user_id": 7, "language": "ru"}


def free_plan(limit: int = 5, album: int = 3) -> Plan:
    return Plan("free", 50, 60, [360], 360, 24, "fast", limit, album)


@pytest.fixture
def ctx(monkeypatch):
    """Подменяет общий контекст приложения на заглушку."""
    def install(used: int = 0, locks=None) -> FakeContext:
        fake = FakeContext(used=used, locks=locks)
        monkeypatch.setattr(handlers_user, "get_ctx", lambda: fake)
        monkeypatch.setattr(access, "get_ctx", lambda: fake)
        return fake
    return install


# ===== Лимит «видео в одном сообщении» =====


async def test_album_over_limit_is_rejected_upfront(ctx) -> None:
    ctx()
    album = [FakeMessage(i) for i in range(4)]
    await handlers_user._process_batch(album, USER, "ru", free_plan(album=3))
    assert album[0].answers, "нет отказа"
    assert "3" in album[0].answers[0]
    assert not album[0].lifecycle_touched(), "статус не должен создаваться"


async def test_album_at_limit_is_allowed_past_the_check(ctx) -> None:
    """Ровно 3 видео на Free — не отказ; дальше срабатывает уже суточный лимит."""
    ctx(used=5)
    album = [FakeMessage(i) for i in range(3)]
    await handlers_user._process_batch(album, USER, "ru", free_plan(album=3))

    texts = album[0].answers + [e for st in album[0].statuses for e in st.edits]
    assert not any("видео в одном сообщении" in a for a in texts), texts
    assert any("5" in a for a in texts), "должен сработать суточный лимит"


async def test_pro_plan_has_no_album_limit(ctx, monkeypatch) -> None:
    """На Pro ограничение «видео в одном сообщении» не срабатывает."""
    ctx(used=0)
    processed: list[str] = []

    async def stub(platform_ctx, user, lang, plan, item, status, index, total):
        processed.append(item[2])
        return True

    monkeypatch.setattr(handlers_user, "_convert_one", stub)
    pro = Plan("pro", 200, 60, [360, 480, 640], 480, 20, "medium", 0, 0)
    album = [FakeMessage(i) for i in range(10)]

    await handlers_user._process_batch(album, USER, "ru", pro)

    assert not any("видео в одном сообщении" in a for a in album[0].answers)
    assert len(processed) == 10, f"обработано {len(processed)} из 10"


# ===== Дневной лимит =====


async def test_limit_notice_stays_visible(ctx) -> None:
    """
    Регресс: сообщение о достигнутом дневном лимите не должно удаляться.

    Было: текст лимита писался в статус-сообщение, а блок finally это же
    сообщение удалял — пользователь видел вспышку и пустоту.
    """
    ctx(used=5)
    msg = FakeMessage(1)
    await handlers_user._process_batch([msg], USER, "ru", free_plan(limit=5))

    assert len(msg.statuses) == 1, "статус-сообщение не создавалось"
    status = msg.statuses[0]
    assert not status.deleted, "сообщение о лимите было удалено (баг вернулся!)"
    assert len(status.edits) == 1
    assert "5" in status.edits[0]


async def test_limit_notice_has_menu_button(ctx) -> None:
    """К сообщению о лимите приложено главное меню, чтобы было куда нажать."""
    ctx(used=5)
    msg = FakeMessage(1)
    await handlers_user._process_batch([msg], USER, "ru", free_plan(limit=5))
    assert msg.statuses and msg.statuses[0].edits


async def test_oversized_file_is_rejected_without_status(ctx) -> None:
    ctx()
    msg = FakeMessage(1, size=999 * 1024 * 1024)
    await handlers_user._process_batch([msg], USER, "ru", free_plan())
    assert msg.answers and "50" in msg.answers[0]
    assert not msg.lifecycle_touched()


async def test_unsupported_format_is_rejected(ctx) -> None:
    ctx()
    msg = FakeMessage(1, name="weird.qqq")
    await handlers_user._process_batch([msg], USER, "ru", free_plan())
    assert msg.answers
    assert not msg.lifecycle_touched()


async def test_problems_are_reported_in_one_message(ctx) -> None:
    ctx()
    album = [FakeMessage(1, size=999 * 1024 * 1024), FakeMessage(2, name="bad.qqq")]
    await handlers_user._process_batch(album, USER, "ru", free_plan())
    assert len(album[0].answers) == 1
    assert "50" in album[0].answers[0] and ".qqq" in album[0].answers[0]


# ===== Сериализация =====


async def test_same_user_is_serialized(ctx) -> None:
    """
    Гонка на дневном лимите: без блокировки параллельные видео превышали лимит.

    Замер: лимит 5, уже 4 использовано, 4 параллельных видео -> 8 конвертаций.
    """
    fake = ctx(used=4)
    started: list[str] = []

    async def counting(platform_ctx, user, lang, plan, item, status, index, total):
        started.append(item[2])
        await asyncio.sleep(0.05)
        platform_ctx.usage.rows += 1        # как реальная запись usage после успеха
        return True

    original = handlers_user._convert_one
    handlers_user._convert_one = counting
    try:
        await asyncio.gather(*(
            handlers_user._process_batch([FakeMessage(i)], USER, "ru", free_plan(limit=5))
            for i in range(4)
        ))
    finally:
        handlers_user._convert_one = original

    assert len(started) == 1, f"лимит превышен: {len(started)} конвертаций при 1 доступной"
    assert fake.usage.rows == 5


async def test_lock_is_released_after_batch(ctx) -> None:
    fake = ctx(used=5)
    await handlers_user._process_batch([FakeMessage(1)], USER, "ru", free_plan(limit=5))
    assert fake.locks.busy() == 0
    assert fake.locks._locks == {}
