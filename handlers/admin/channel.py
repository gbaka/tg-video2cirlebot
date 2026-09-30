"""
Канал/чат, членство в котором проверяется
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import menus
from access import ensure_user
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
    text += t(lang, "channel.current", link=link) if link else t(lang, "channel.not_set")
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
        text += t(lang, "channel.current", link=link) if link else t(lang, "channel.not_set")
        text += t(lang, "channel.usage")
        await message.answer(text, parse_mode="HTML")
        return

    checker = ctx.channel_checker
    old = checker.channel_link
    checker.set_link(args)
    chat_id = checker.chat_id
    if chat_id is None:
        checker.set_link(old)
        await message.answer(t(lang, "channel.bad_link"), parse_mode="HTML")
        return
    try:
        chat = await ctx.bot.get_chat(chat_id)
        await ctx.channel.set_link(args)
        await message.answer(
            f"✅ <b>{chat.title}</b>\n🔗 <code>{args}</code>\n🔐 {chat.type}\n"
            f"👥 {getattr(chat, 'participants_count', '?')}",
            parse_mode="HTML",
        )
    except Exception as e:
        checker.set_link(old)
        await message.answer(
            f"❌ Не удалось получить доступ к каналу/чату.\n<code>{e}</code>", parse_mode="HTML"
        )


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
    chat_id = ctx.channel_checker.chat_id
    if chat_id is None:
        await message.answer(t(lang, "channel.bad_link"), parse_mode="HTML")
        return
    try:
        chat = await ctx.bot.get_chat(chat_id)
        await message.answer(
            f"📋 <b>{chat.title}</b>\n🔗 <code>{link}</code>\n🆔 <code>{chat.id}</code>\n"
            f"🔐 {chat.type}",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ <code>{e}</code>", parse_mode="HTML")


@router.message(Command("clearchannel"), private_chat)
async def cmd_clear_channel(message: Message) -> None:
    if not await admin_guard(message):
        return
    ctx = get_ctx()
    checker = ctx.channel_checker
    await ctx.channel.set_link("")
    checker.set_link("")
    await message.answer("✅ Проверка подписки отключена.")
