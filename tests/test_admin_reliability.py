"""Admin HTML and command pagination regressions."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

from handlers.admin import channel, stats, users


async def test_tasks_escape_filename(monkeypatch):
    task = NS(user_id=7,filename="<broken>.mp4",size=100)
    ctx = NS(tasks=NS(all=lambda:[task],elapsed=lambda task:1),jobs=NS(jobs=[]),
             albums=NS(pending=lambda:0))
    monkeypatch.setattr(stats,"get_ctx",lambda:ctx)
    text = await stats._render_tasks("ru")
    assert "<broken>" not in text
    assert "&lt;broken&gt;" in text


async def test_tasks_stay_within_message_budget(monkeypatch):
    tasks = [NS(user_id=i, filename="<&" * 20, size=100) for i in range(100)]
    ctx = NS(tasks=NS(all=lambda: tasks, elapsed=lambda task: 1),
             jobs=NS(jobs=[]), albums=NS(pending=lambda: 0))
    monkeypatch.setattr(stats, "get_ctx", lambda: ctx)
    assert len(await stats._render_tasks("ru")) <= 4096


async def test_finduser_persists_query(monkeypatch):
    state = NS(clear=AsyncMock(),update_data=AsyncMock(),set_state=AsyncMock())
    monkeypatch.setattr(users,"admin_guard",AsyncMock(return_value=True))
    monkeypatch.setattr(users,"ensure_user",AsyncMock(return_value={"language":"ru"}))
    monkeypatch.setattr(users,"_render_users",AsyncMock(return_value=("users",None)))
    msg=NS(answer=AsyncMock())
    await users.cmd_find_user(msg,CommandObject(command="finduser",args="bob"),state)
    state.update_data.assert_awaited_once_with(query="bob")


async def test_setchannel_escapes_title(monkeypatch):
    from channel_checker import ChannelChecker
    bot=NS(get_chat=AsyncMock(return_value=NS(title="<broken>",type="channel",id=-1001234567890,
                                              invite_link="https://t.me/+abcdef")))
    checker=ChannelChecker(bot,"@old_name")
    repo=NS(set_link=AsyncMock(),set_with_invite=AsyncMock())
    ctx=NS(bot=bot,channel=repo,channel_checker=checker)
    monkeypatch.setattr(channel,"admin_guard",AsyncMock(return_value=True))
    monkeypatch.setattr(channel,"ensure_user",AsyncMock(return_value={"language":"ru"}))
    monkeypatch.setattr(channel,"get_ctx",lambda:ctx)
    msg=NS(answer=AsyncMock())
    await channel.cmd_set_channel(msg,CommandObject(command="setchannel",args="@new_name"))
    text=msg.answer.call_args.args[0]
    assert "<broken>" not in text
    assert "&lt;broken&gt;" in text


async def test_confirmation_failure_does_not_revert_committed_channel(monkeypatch):
    from channel_checker import ChannelChecker
    bot = NS(get_chat=AsyncMock(return_value=NS(
        title="New", type="channel", invite_link="https://t.me/+abcdef",
    )))
    checker = ChannelChecker(bot, "@old_name")
    repo = NS(set_with_invite=AsyncMock())
    ctx = NS(bot=bot, channel=repo, channel_checker=checker)
    monkeypatch.setattr(channel, "admin_guard", AsyncMock(return_value=True))
    monkeypatch.setattr(channel, "ensure_user", AsyncMock(return_value={"language": "ru"}))
    monkeypatch.setattr(channel, "get_ctx", lambda: ctx)
    msg = NS(answer=AsyncMock(side_effect=RuntimeError("send failed")))
    with pytest.raises(RuntimeError, match="send failed"):
        await channel.cmd_set_channel(msg, CommandObject(command="setchannel", args="@new_name"))
    assert checker.channel_link == "@new_name"
    repo.set_with_invite.assert_awaited_once()


@pytest.mark.parametrize("value", ["²", "--1", "999999999999999999999999999999"])
async def test_revoke_malformed_id_is_reported_not_raised(monkeypatch, value):
    from handlers.admin import cards
    monkeypatch.setattr(cards, "admin_guard", AsyncMock(return_value=True))
    monkeypatch.setattr(cards, "ensure_user", AsyncMock(return_value={"language": "ru"}))
    called = AsyncMock()
    monkeypatch.setattr(cards, "cancel_subscription", called)
    monkeypatch.setattr(cards, "get_ctx", lambda: NS())
    msg = NS(answer=AsyncMock())
    await cards.cmd_revoke(msg, CommandObject(command="revoke", args=value))
    called.assert_not_awaited()
    msg.answer.assert_awaited_once()


async def test_numeric_looking_unicode_search_does_not_crash(db):
    from repositories import UserRepo
    users = UserRepo(db)
    await users.get_or_create(42, None, "²")
    rows, total = await users.search_page(0, 10, "²")
    assert total == 1 and rows[0]["user_id"] == 42


async def test_out_of_range_numeric_search_is_empty(db):
    from repositories import UserRepo
    assert await UserRepo(db).search_page(0, 10, "9" * 100) == ([], 0)


async def test_private_channel_without_invite_is_not_saved(monkeypatch):
    from channel_checker import ChannelChecker
    bot = NS(get_chat=AsyncMock(return_value=NS(title="Private", invite_link=None)))
    repo = NS(set_with_invite=AsyncMock())
    checker = ChannelChecker(bot, "@old_name")
    monkeypatch.setattr(channel, "admin_guard", AsyncMock(return_value=True))
    monkeypatch.setattr(channel, "ensure_user", AsyncMock(return_value={"language": "ru"}))
    monkeypatch.setattr(channel, "get_ctx", lambda: NS(
        bot=bot, channel=repo, channel_checker=checker,
    ))
    await channel.cmd_set_channel(NS(answer=AsyncMock()), CommandObject(
        command="setchannel", args="-1001234567890",
    ))
    repo.set_with_invite.assert_not_awaited()
    assert checker.channel_link == "@old_name"

