"""
Вход в админское меню
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import menus
from access import ensure_user
from handlers.admin.shared import (
    admin_guard,
    private_chat,
)
from i18n import t
from tg import cb_message

logger = logging.getLogger(__name__)
router = Router(name="admin.menu")

@router.callback_query(F.data == "m:admin")
async def cb_admin(cb: CallbackQuery) -> None:
    if not await admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        t(lang, "admin.title"), reply_markup=menus.admin_menu(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.message(Command("admin"), private_chat)
async def cmd_admin(message: Message) -> None:
    if not await admin_guard(message):
        return
    user = await ensure_user(message)
    lang = user["language"]
    await message.answer(
        t(lang, "admin.title"), reply_markup=menus.admin_menu(lang), parse_mode="HTML"
    )
