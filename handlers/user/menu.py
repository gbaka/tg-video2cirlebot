"""
Команды и навигация по главному меню
"""

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
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
async def cmd_menu(message: Message, state: FSMContext | None = None) -> None:
    if state is not None:
        await state.clear()
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


@router.message(Command("terms"), private_chat)
async def cmd_terms(message: Message) -> None:
    user = await ensure_user(message)
    await message.answer(t(user["language"], "support.terms"), parse_mode="HTML")


@router.message(Command("paysupport"), private_chat)
async def cmd_paysupport(message: Message, command: CommandObject) -> None:
    user = await ensure_user(message)
    lang = user["language"]
    body = (command.args or "").strip()
    if not body:
        await message.answer(t(lang, "support.instructions"), parse_mode="HTML")
        return
    ctx = get_ctx()
    delivered = False
    # Never include payment credentials; user supplies the issue and their ID is attached.
    text = (f"Payment support · <code>{user['user_id']}</code>\n"
            f"{escape(body[:1500])}\n\n"
            f"<code>/supportreply {user['user_id']} &lt;text&gt;</code>")
    for admin_id in ctx.config.bot.admin_ids:
        try:
            await ctx.bot.send_message(admin_id, text, parse_mode="HTML")
            delivered = True
        except Exception:
            logger.warning("Could not deliver support request to admin %s", admin_id)
    key = "support.sent" if delivered else "support.unavailable"
    await message.answer(t(lang, key), parse_mode="HTML")


@router.message(Command("supportreply"), private_chat)
async def cmd_supportreply(message: Message, command: CommandObject) -> None:
    user = await ensure_user(message)
    lang = user["language"]
    if not is_admin(user["user_id"]):
        await message.answer(t(lang, "admin.denied"))
        return
    parts = (command.args or "").split(maxsplit=1)
    try:
        target_id = int(parts[0])
        body = parts[1].strip()
        if not body or not await get_ctx().users.get(target_id):
            raise ValueError("Unknown user")
    except (ValueError, IndexError):
        await message.answer(t(lang, "support.reply_usage"), parse_mode="HTML")
        return
    target = await get_ctx().users.get(target_id)
    target_lang = (target or {}).get("language", "ru")
    try:
        await get_ctx().bot.send_message(
            target_id, t(target_lang, "support.reply", text=escape(body[:1500])), parse_mode="HTML",
        )
    except Exception:
        await message.answer(t(lang, "support.unavailable"))
        return
    await message.answer(t(lang, "support.reply_sent"))


# ===== Навигация по меню =====

@router.callback_query(F.data == "m:main")
async def cb_main(cb: CallbackQuery, state: FSMContext | None = None) -> None:
    if state is not None:
        await state.clear()
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
