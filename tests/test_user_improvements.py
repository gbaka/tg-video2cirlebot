"""User controls and payment support remain reachable without conversion access."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

import menus
from handlers.user import menu, settings
from locks import UserLocks


async def test_paysupport_relays_request_to_admin(monkeypatch, support):
    fn = getattr(menu, "cmd_paysupport", None)
    assert fn is not None
    bot = NS(send_message=AsyncMock())
    ctx = NS(bot=bot, config=NS(bot=NS(admin_ids=[999])), locks=UserLocks(),
             support=support)
    monkeypatch.setattr(menu, "get_ctx", lambda: ctx)
    monkeypatch.setattr(menu, "ensure_user", AsyncMock(return_value={"user_id": 7,
                                                                       "language": "ru"}))
    msg = NS(answer=AsyncMock())
    await fn(msg, CommandObject(command="paysupport", args="<broken> payment"))
    bot.send_message.assert_awaited_once()
    assert "&lt;broken&gt;" in bot.send_message.call_args.args[1]
    msg.answer.assert_awaited_once()


async def test_terms_handler_is_available(monkeypatch):
    fn = getattr(menu, "cmd_terms", None)
    assert fn is not None
    monkeypatch.setattr(menu, "ensure_user", AsyncMock(return_value={"language": "en"}))
    msg = NS(answer=AsyncMock())
    await fn(msg)
    assert "/paysupport" in msg.answer.call_args.args[0]


async def test_fragment_command_persists_valid_selection(monkeypatch):
    fn = getattr(settings, "cmd_fragment", None)
    assert fn is not None
    repo = NS(set_video_options=AsyncMock())
    monkeypatch.setattr(settings, "get_ctx", lambda: NS(users=repo))
    user = {"user_id": 7, "language": "ru"}
    monkeypatch.setattr(settings, "ensure_user", AsyncMock(return_value=user))
    monkeypatch.setattr(
        settings, "user_plan", AsyncMock(return_value=(NS(max_duration_sec=60), None)),
    )
    msg = NS(answer=AsyncMock())
    await fn(msg, CommandObject(command="fragment", args="01:20 30"))
    repo.set_video_options.assert_awaited_once_with(7, trim_start=80.0, trim_duration=30.0)


async def test_invalid_fragment_does_not_change_settings(monkeypatch):
    fn = getattr(settings, "cmd_fragment", None)
    assert fn is not None
    repo = NS(set_video_options=AsyncMock())
    monkeypatch.setattr(settings, "get_ctx", lambda: NS(users=repo))
    user = {"user_id": 7, "language": "ru"}
    monkeypatch.setattr(settings, "ensure_user", AsyncMock(return_value=user))
    monkeypatch.setattr(
        settings, "user_plan", AsyncMock(return_value=(NS(max_duration_sec=60), None)),
    )
    msg = NS(answer=AsyncMock())
    await fn(msg, CommandObject(command="fragment", args="0 999"))
    repo.set_video_options.assert_not_awaited()
    msg.answer.assert_awaited_once()


@pytest.mark.parametrize("pro", [False, True])
def test_video_controls_available_to_all_plans(pro):
    callbacks = {
        b.callback_data for row in menus.settings_menu("ru", pro).inline_keyboard for b in row
    }
    assert {"m:framing", "m:fragment"} <= callbacks


async def test_support_does_not_claim_delivery_when_admins_unreachable(monkeypatch, support):
    monkeypatch.setattr(menu, "ensure_user", AsyncMock(return_value={
        "user_id": 7, "language": "ru",
    }))
    monkeypatch.setattr(menu, "get_ctx", lambda: NS(
        config=NS(bot=NS(admin_ids=[1, 2])),
        bot=NS(send_message=AsyncMock(side_effect=RuntimeError("blocked"))),
        locks=UserLocks(),
        support=support,
    ))
    message = NS(answer=AsyncMock())
    await menu.cmd_paysupport(message, CommandObject(command="paysupport", args="help"))
    assert "не удалось" in message.answer.call_args.args[0]


async def test_support_reply_is_admin_only(monkeypatch):
    monkeypatch.setattr(menu, "ensure_user", AsyncMock(return_value={
        "user_id": 7, "language": "ru",
    }))
    monkeypatch.setattr(menu, "is_admin", lambda _: False)
    def no_context():
        raise AssertionError("must not send anything")
    monkeypatch.setattr(menu, "get_ctx", no_context)
    await menu.cmd_supportreply(NS(answer=AsyncMock()), CommandObject(
        command="supportreply", args="1 secret",
    ))

