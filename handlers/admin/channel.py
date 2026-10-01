"""
Канал/чат, членство в котором проверяется
"""

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import menus
from access import ensure_user
from channel_checker import ChannelChecker
from context import get_ctx
from handlers.admin.shared import (
    admin_guard,
    private_chat,
)
from i18n import t
from tg import cb_message

logger = logging.getLogger(__name__)
router = Router(name="admin.channel")

@router.callback_query(F.data == "a:chan")
async def cb_channel(cb: CallbackQuery) -> None:
    if not await admin_guard(cb):
        return
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    link = await ctx.channel.get_link()
    text = t(lang, "channel.title")
    text += t(lang, "channel.current", link=escape(link)) if link else t(lang, "channel.not_set")
    text += t(lang, "channel.usage")
    await cb_message(cb).edit_text(
        text, reply_markup=menus.channel_menu(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.message(Command("setchannel"), private_chat)
async def cmd_set_channel(message: Message, command: CommandObject) -> None:
    if not await admin_guard(message):
        return
    ctx = get_ctx()
    user = await ensure_user(message)
    lang = user["language"]
    args = (command.args or "").strip()
    if not args:
        link = await ctx.channel.get_link()
        text = t(lang, "channel.title")
        text += (t(lang, "channel.current", link=escape(link))
                 if link else t(lang, "channel.not_set"))
        text += t(lang, "channel.usage")
        await message.answer(text, parse_mode="HTML")
        return

    parts = args.split()
    if len(parts) not in (1, 2):
        await message.answer(t(lang, "channel.bad_link"))
        return
    identifier = parts[0]
    candidate = ChannelChecker(ctx.bot, identifier)
    if candidate.chat_id is None:
        await message.answer(t(lang, "channel.bad_link"))
        return
    explicit_invite = parts[1] if len(parts) == 2 else ""
    if explicit_invite and not candidate.valid_invite_link(explicit_invite):
        await message.answer(t(lang, "channel.invite_required"))
        return
    try:
        chat = await ctx.bot.get_chat(candidate.chat_id)
    except Exception as exc:
        await message.answer(t(lang, "channel.access_error", error=escape(str(exc)[:400])))
        return
    invite = explicit_invite or getattr(chat, "invite_link", None) or ""
    if invite and not candidate.valid_invite_link(invite):
        invite = ""
    if isinstance(candidate.chat_id, int) and not invite:
        await message.answer(t(lang, "channel.invite_required"))
        return
    try:
        await ctx.channel.set_with_invite(identifier, invite)
    except Exception:
        logger.exception("Could not save membership channel")
        await message.answer(t(lang, "channel.save_error"))
        return
    # Sending the confirmation must never roll back an already committed setting.
    ctx.channel_checker.set_link(identifier)
    ctx.channel_checker.set_invite_link(invite)
    await message.answer(t(
        lang, "channel.saved", title=escape(str(chat.title or "")[:128]),
        link=escape(identifier), chat_type=escape(str(chat.type)),
    ), parse_mode="HTML")


@router.message(Command("channel"), private_chat)
async def cmd_show_channel(message: Message) -> None:
    if not await admin_guard(message):
        return
    ctx = get_ctx()
    user = await ensure_user(message)
    lang = user["language"]
    link = await ctx.channel.get_link()
    if not link:
        await message.answer(t(lang, "channel.not_set"), parse_mode="HTML")
        return
    candidate = ChannelChecker(ctx.bot, link)
    chat_id = candidate.chat_id
    if chat_id is None:
        await message.answer(t(lang, "channel.bad_link"), parse_mode="HTML")
        return
    try:
        chat = await ctx.bot.get_chat(chat_id)
    except Exception as exc:
        await message.answer(t(lang, "channel.access_error", error=escape(str(exc)[:400])))
        return
    await message.answer(
        t(lang, "channel.saved", title=escape(str(chat.title or "")[:128]),
          link=escape(link), chat_type=escape(str(chat.type)))
        + f"\n🆔 <code>{chat.id}</code>", parse_mode="HTML",
    )


@router.message(Command("clearchannel"), private_chat)
async def cmd_clear_channel(message: Message) -> None:
    if not await admin_guard(message):
        return
    ctx = get_ctx()
    checker = ctx.channel_checker
    await ctx.channel.set_link("")
    checker.set_link("")
    user = await ensure_user(message)
    await message.answer(t(user["language"], "channel.cleared"))
