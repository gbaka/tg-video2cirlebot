"""
Настройки бота (переключатели)
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

import menus
from access import ensure_user
from context import get_ctx
from handlers.admin.shared import (
    admin_guard,
)
from i18n import t
from tg import cb_data, cb_message

logger = logging.getLogger(__name__)
router = Router(name="admin.settings")

async def _render_botset(lang: str) -> tuple[str, bool, bool]:
    ctx = get_ctx()
    maintenance = await ctx.settings.get_bool("maintenance", False)
    membership = await ctx.settings.get_bool("membership_check", True)
    text = t(lang, "botset.title") + t(
        lang, "botset.body",
        maintenance=t(lang, "btn.on") if maintenance else t(lang, "btn.off"),
        membership=t(lang, "btn.on") if membership else t(lang, "btn.off"),
    )
    return text, maintenance, membership


@router.callback_query(F.data == "a:bset")
async def cb_botset(cb: CallbackQuery) -> None:
    if not await admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    text, maint, memb = await _render_botset(lang)
    await cb_message(cb).edit_text(
        text, reply_markup=menus.bot_settings_menu(lang, maint, memb), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bs:"))
async def cb_toggle_setting(cb: CallbackQuery) -> None:
    if not await admin_guard(cb):
        return
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    key = cb_data(cb).split(":", 1)[1]
    mapping = {"maintenance": ("maintenance", False), "membership": ("membership_check", True)}
    if key not in mapping:
        await cb.answer()
        return
    setting_key, default = mapping[key]
    current = await ctx.settings.get_bool(setting_key, default)
    await ctx.settings.set(setting_key, "false" if current else "true")
    text, maint, memb = await _render_botset(lang)
    await cb_message(cb).edit_text(
        text, reply_markup=menus.bot_settings_menu(lang, maint, memb), parse_mode="HTML"
    )
    await cb.answer(t(lang, "admin.setting_saved"))
