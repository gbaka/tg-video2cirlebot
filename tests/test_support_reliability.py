"""Anti-spam limits for /paysupport: cooldown, daily cap and parallel sends."""

import asyncio
from types import SimpleNamespace

import pytest

import handlers.user.menu as menu
from tests.fakes import FakeContext, FakeMessage

USER = {"user_id": 42, "language": "ru"}


class Usage:
    async def count_user_today(self, _user_id):
        return 0


def make_ctx(support, *, cooldown=120, daily=3, admins=(999,)):
    ctx = FakeContext()
    ctx.usage = Usage()
    ctx.support = support
    ctx.config.bot = SimpleNamespace(admin_ids=list(admins))
    ctx.config.support = SimpleNamespace(cooldown_sec=cooldown, daily_limit=daily)
    ctx.config.default_language = "ru"
    ctx.delivered = []

    async def send_message(chat_id, text, **_kwargs):
        ctx.delivered.append((chat_id, text))

    ctx.bot = SimpleNamespace(send_message=send_message)
    return ctx


@pytest.fixture
def ctx(support, monkeypatch):
    context = make_ctx(support)

    async def ensure_user(_event):
        return dict(USER)

    monkeypatch.setattr(menu, "get_ctx", lambda: context)
    monkeypatch.setattr(menu, "ensure_user", ensure_user)
    return context


async def call(args, msg=None):
    msg = msg or FakeMessage()
    await menu.cmd_paysupport(msg, SimpleNamespace(args=args))
    return msg


async def test_first_request_is_forwarded(ctx):
    msg = await call("не пришло Pro после оплаты")
    assert len(ctx.delivered) == 1
    assert "42" in ctx.delivered[0][1]
    assert msg.answers[-1] == menu.t("ru", "support.sent")


async def test_second_request_within_cooldown_is_blocked(ctx):
    await call("первое обращение")
    msg = await call("спам")
    assert len(ctx.delivered) == 1, "второе обращение дошло до администраторов"
    assert msg.answers[-1] == menu.t("ru", "support.rate_limited", seconds=120)


async def test_daily_limit_blocks_after_cooldown_expires(ctx, support):
    """With no cooldown the daily cap is the only guard."""
    ctx.config.support = SimpleNamespace(cooldown_sec=0, daily_limit=2)
    for index in range(2):
        await call(f"вопрос {index}")
    msg = await call("третье сверх лимита")
    assert len(ctx.delivered) == 2
    assert msg.answers[-1] == menu.t("ru", "support.daily_limit", limit=2)


async def test_zero_daily_limit_means_unlimited(ctx, support):
    ctx.config.support = SimpleNamespace(cooldown_sec=0, daily_limit=0)
    for index in range(7):
        await call(f"вопрос {index}")
    assert len(ctx.delivered) == 7
    assert await support.count_today(USER["user_id"]) == 7


async def test_usage_hint_does_not_consume_quota(ctx, support):
    msg = await call("")
    assert not ctx.delivered
    assert await support.count_today(USER["user_id"]) == 0
    assert "{cooldown}" not in msg.answers[-1] and "{daily}" not in msg.answers[-1]


async def test_parallel_requests_send_only_one_message(ctx, support):
    """Ten simultaneous commands must not reach the admins ten times."""
    await asyncio.gather(*(call(f"вопрос {index}") for index in range(10)))
    assert len(ctx.delivered) == 1, f"доставлено {len(ctx.delivered)} вместо 1"
    assert await support.count_today(USER["user_id"]) == 1


async def test_failed_delivery_does_not_restore_quota(ctx, support):
    async def broken(_chat_id, _text, **_kwargs):
        raise RuntimeError("telegram down")

    ctx.bot = SimpleNamespace(send_message=broken)
    msg = await call("оплатил, ответа нет")
    assert msg.answers[-1] == menu.t("ru", "support.unavailable")
    assert await support.count_today(USER["user_id"]) == 1


async def test_cooldown_is_per_user(ctx, support, monkeypatch):
    async def other_user(_event):
        return {"user_id": 77, "language": "ru"}

    await call("первый пользователь")
    monkeypatch.setattr(menu, "ensure_user", other_user)
    await call("второй пользователь")
    assert len(ctx.delivered) == 2
    assert await support.count_today(42) == 1
    assert await support.count_today(77) == 1


async def test_limits_come_from_config(ctx):
    ctx.config.support = SimpleNamespace(cooldown_sec=3600, daily_limit=1)
    await call("первое")
    msg = await call("второе")
    assert msg.answers[-1] == menu.t("ru", "support.rate_limited", seconds=3600)


def test_config_defaults_and_validation(tmp_path):
    from config_loader import Config

    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    defaults = Config.load(empty).support
    assert defaults.cooldown_sec == 120
    assert defaults.daily_limit == 3

    path = tmp_path / "config.yaml"
    valid_bot = 'bot:\n  token: "123:ABC"\n  admin_ids: [1]\n'

    path.write_text(valid_bot + "support:\n  cooldown_sec: -5\n  daily_limit: 500\n",
                    encoding="utf-8")
    errors = Config.load(path).validate()
    assert any("support.cooldown_sec" in error for error in errors)
    assert any("support.daily_limit" in error for error in errors)

    path.write_text(valid_bot + "support:\n  cooldown_sec: 0\n  daily_limit: 0\n",
                    encoding="utf-8")
    assert Config.load(path).validate() == []

    path.write_text(valid_bot + "support:\n  cooldown_sec: 'soon'\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"support\.cooldown_sec"):
        Config.load(path)
