"""
Сервисы работают без Telegram и без хендлеров.

Это и есть смысл выноса логики из хендлеров: операции с подписками можно
вызвать из скрипта обслуживания или теста, подсунув контекст целиком.
"""

import os
from typing import Any, cast

import pytest

from context import AppContext
from repositories import LIFETIME_EXPIRES, SubscriptionRepo, UserRepo
from services import export, subscriptions
from tests.fakes import FakeContext


class RecordingBot:
    """Бот-заглушка: запоминает исходящие сообщения."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail = fail

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("бот заблокирован пользователем")
        self.sent.append((chat_id, text))


def make_ctx(db, bot: RecordingBot | None = None) -> AppContext:
    """Контекст с настоящими репозиториями, но без Telegram."""
    fake = FakeContext()
    fake.users = UserRepo(db)
    fake.subs = SubscriptionRepo(db)
    fake.bot = bot or RecordingBot()
    return cast(AppContext, fake)


@pytest.fixture
async def member(db, users: UserRepo) -> dict[str, Any]:
    """Пользователь, который уже писал боту (язык ru по умолчанию)."""
    return await users.get_or_create(
        user_id=42, username="bob", first_name="Bob", default_language="ru"
    )


async def test_gift_activates_pro(db, member) -> None:
    ctx = make_ctx(db)
    expires = await subscriptions.gift_subscription(ctx, member["user_id"], 30, False)
    assert expires, "подписка не выдана"
    sub = await ctx.subs.get_active(member["user_id"])
    assert sub is not None and sub["plan"] == "pro"


async def test_gift_to_unknown_user_returns_none(db) -> None:
    """Пользователь не писал боту — подарить ему нельзя."""
    ctx = make_ctx(db)
    assert await subscriptions.gift_subscription(ctx, 999_999, 30, False) is None


async def test_lifetime_gift_is_forever(db, member) -> None:
    ctx = make_ctx(db)
    expires = await subscriptions.gift_subscription(ctx, member["user_id"], None, True)
    assert expires == LIFETIME_EXPIRES
    assert subscriptions.expires_label("ru", expires) != "—"


async def test_gift_then_cancel(db, member) -> None:
    ctx = make_ctx(db)
    await subscriptions.gift_subscription(ctx, member["user_id"], 30, False)

    cancelled = await subscriptions.cancel_subscription(ctx, member["user_id"])
    assert cancelled is not None, "подписка не отменена"
    assert await ctx.subs.get_active(member["user_id"]) is None


async def test_cancel_without_subscription_returns_none(db, member) -> None:
    ctx = make_ctx(db)
    assert await subscriptions.cancel_subscription(ctx, member["user_id"]) is None


async def test_gift_notification_goes_to_recipient_in_his_language(db, member) -> None:
    ctx = make_ctx(db)
    expires = await subscriptions.gift_subscription(ctx, member["user_id"], 30, False)
    shown = subscriptions.expires_label("ru", expires)
    await subscriptions.notify_gift(ctx, member["user_id"], shown)

    assert ctx.bot.sent, "уведомление не отправлено"
    chat_id, text = ctx.bot.sent[-1]
    assert chat_id == member["user_id"]
    assert "Pro" in text


async def test_notification_failure_does_not_break_the_flow(db, member) -> None:
    """Заблокировал бота — подписка всё равно выдана, исключения наружу нет."""
    ctx = make_ctx(db, bot=RecordingBot(fail=True))
    expires = await subscriptions.gift_subscription(ctx, member["user_id"], 30, False)
    await subscriptions.notify_gift(ctx, member["user_id"], str(expires))
    assert await ctx.subs.get_active(member["user_id"]) is not None


# ===== Разбор аргументов /gift =====


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], None),
        (["abc"], None),
        (["42"], (42, None, False)),
        (["42", "10"], (42, 10, False)),
        (["42", "life"], (42, None, True)),
        (["42", "навсегда"], (42, None, True)),
        (["42", "0"], (42, 30, False)),      # 0 дней -> срок по умолчанию
        (["42", "abc"], None),
        (["-100123"], (-100123, None, False)),
    ],
)
def test_parse_gift_args(args: list[str], expected) -> None:
    assert subscriptions.parse_gift_args(args) == expected


def test_expires_label() -> None:
    assert subscriptions.expires_label("ru", None) == "—"
    assert subscriptions.expires_label("ru", LIFETIME_EXPIRES) not in ("—", "")
    assert subscriptions.expires_label("ru", "2030-05-06 12:00:00") == "2030-05-06"


# ===== Экспорт =====


async def test_csv_export_contains_users(db, member) -> None:
    ctx = make_ctx(db)
    path, count = await export.write_users_csv(ctx)

    assert count == 1, f"выгружено записей: {count}"
    with open(path, encoding="utf-8-sig") as fh:
        rows = fh.read().splitlines()
    assert rows[0].split(";")[0] == "user_id"
    assert "bob" in rows[1]

    os.unlink(path)


def test_csv_expires_marks_lifetime() -> None:
    assert export.csv_expires(None) == ""
    assert export.csv_expires(LIFETIME_EXPIRES) == "lifetime"
    assert export.csv_expires("2030-05-06 12:00:00") == "2030-05-06"
