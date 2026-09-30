"""
Команды и навигация по главному меню
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    Message,
)

import menus
from access import (
    ensure_user,
    is_admin,
    user_lang,
    user_plan,
)
from context import get_ctx
from handlers.user.shared import guard, private_chat, show_main
from i18n import t
from tg import cb_message

logger = logging.getLogger(__name__)
router = Router(name="user.menu")

@router.message(Command("start"), private_chat)
async def cmd_start(message: Message, command: CommandObject) -> None:
    user = await ensure_user(message)
    lang = await user_lang(user)
    if not await guard(message, lang):
        return
    await show_main(message, user)


@router.message(Command("menu"), private_chat)
async def cmd_menu(message: Message) -> None:
    user = await ensure_user(message)
    lang = await user_lang(user)
    if not await guard(message, lang):
        return
    await show_main(message, user)


def _help_text(lang: str, user: dict) -> str:
    """Справка; админские команды показываем только администраторам."""
    text = t(lang, "help.text")
    uid = user.get("user_id")
    if uid is not None and is_admin(uid):
        text += t(lang, "help.admin")
    return text


@router.message(Command("help"), private_chat)
async def cmd_help(message: Message) -> None:
    user = await ensure_user(message)
    lang = await user_lang(user)
    if not await guard(message, lang):
        return
    await message.answer(
        _help_text(lang, user),
        reply_markup=menus.back_main(lang),
        parse_mode="HTML",
    )


@router.message(Command("setlanguage"), private_chat)
async def cmd_setlanguage(message: Message) -> None:
    user = await ensure_user(message)
    lang = await user_lang(user)
    await message.answer(
        t(lang, "lang.title"),
        reply_markup=menus.language_menu(lang, lang),
        parse_mode="HTML",
    )


# ===== Навигация по меню =====

@router.callback_query(F.data == "m:main")
async def cb_main(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    await show_main(cb, user)
    await cb.answer()


@router.callback_query(F.data == "m:info")
async def cb_info(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        t(lang, "info.text"), reply_markup=menus.back_main(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "m:help")
async def cb_help(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        _help_text(lang, user), reply_markup=menus.back_main(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "m:profile")
async def cb_profile(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    plan, _ = await user_plan(user)

    today = await ctx.usage.count_user_today(user["user_id"])
    total = await ctx.usage._scalar(
        "SELECT COUNT(*) FROM usage WHERE user_id = ? AND status = 'ok'", (user["user_id"],)
    )
    if plan.code == "pro":
        quality = t(lang, "profile.quality_selectable",
                    res=plan.normalize_resolution(user.get("quality")))
    else:
        quality = t(lang, "profile.quality_fixed", res=plan.default_resolution)

    limit_str = (
        "" if plan.is_unlimited()
        else t(lang, "profile.today_limit", limit=plan.daily_limit)
    )
    plan_label = "⭐ Pro" if plan.code == "pro" else "Free"

    text = t(lang, "profile.title") + t(
        lang, "profile.body",
        user_id=user["user_id"], plan=plan_label, quality=quality,
        today=today, today_limit=limit_str, total=total,
        created=(user.get("created_at") or "")[:10],
    )
    await cb_message(cb).edit_text(text, reply_markup=menus.back_main(lang), parse_mode="HTML")
    await cb.answer()
