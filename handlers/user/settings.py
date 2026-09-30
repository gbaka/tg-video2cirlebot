"""
Настройки пользователя: язык и качество
"""

import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
)

import menus
from access import (
    ensure_user,
    user_plan,
)
from context import get_ctx
from i18n import SUPPORTED, lang_name, t
from tg import cb_data, cb_message

logger = logging.getLogger(__name__)
router = Router(name="user.settings")

@router.callback_query(F.data == "m:settings")
async def cb_settings(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    plan, _ = await user_plan(user)
    quality = (
        t(lang, "profile.quality_selectable", res=plan.normalize_resolution(user.get("quality")))
        if plan.code == "pro"
        else t(lang, "profile.quality_fixed", res=plan.default_resolution)
    )
    text = t(lang, "settings.title", lang=lang_name(lang), quality=quality)
    if plan.code != "pro":
        text += t(lang, "settings.quality_locked")
    await cb_message(cb).edit_text(
        text, reply_markup=menus.settings_menu(lang, plan.code == "pro"), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "m:lang")
async def cb_lang(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        t(lang, "lang.title"), reply_markup=menus.language_menu(lang, lang), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("l:"))
async def cb_set_lang(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    new_lang = cb_data(cb).split(":", 1)[1]
    if new_lang not in SUPPORTED:
        await cb.answer()
        return
    user = await ensure_user(cb)
    await ctx.users.set_language(user["user_id"], new_lang)
    await cb.answer(t(new_lang, "lang.changed", lang=lang_name(new_lang)))
    # Перерисовываем меню настроек на новом языке
    user["language"] = new_lang
    plan, _ = await user_plan(user)
    quality = (
        t(new_lang, "profile.quality_selectable",
          res=plan.normalize_resolution(user.get("quality")))
        if plan.code == "pro"
        else t(new_lang, "profile.quality_fixed", res=plan.default_resolution)
    )
    text = t(new_lang, "settings.title", lang=lang_name(new_lang), quality=quality)
    if plan.code != "pro":
        text += t(new_lang, "settings.quality_locked")
    await cb_message(cb).edit_text(
        text,
        reply_markup=menus.settings_menu(new_lang, plan.code == "pro"),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "m:quality")
async def cb_quality(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    plan, _ = await user_plan(user)
    if plan.code != "pro":
        await cb.answer(t(lang, "settings.quality_locked").strip(), show_alert=True)
        return
    current = plan.normalize_resolution(user.get("quality"))
    await cb_message(cb).edit_text(
        t(lang, "quality.title") + "\n\n" + t(lang, "quality.current", res=current),
        reply_markup=menus.quality_menu(lang, plan.resolutions, current),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("q:"))
async def cb_set_quality(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    plan, _ = await user_plan(user)
    if plan.code != "pro":
        await cb.answer(t(lang, "settings.quality_locked").strip(), show_alert=True)
        return
    try:
        res = int(cb_data(cb).split(":", 1)[1])
    except ValueError:
        await cb.answer()
        return
    res = plan.normalize_resolution(res)
    await ctx.users.set_quality(user["user_id"], res)
    await cb.answer(t(lang, "quality.changed", res=res))
    user["quality"] = res
    await cb_message(cb).edit_text(
        t(lang, "quality.title") + "\n\n" + t(lang, "quality.current", res=res),
        reply_markup=menus.quality_menu(lang, plan.resolutions, res),
        parse_mode="HTML",
    )
