"""
Общее для пользовательских хендлеров: фильтр личных чатов, проверка
членства в канале и вывод главного меню.
"""

from aiogram import F
from aiogram.types import CallbackQuery, Message

import menus
from access import get_invite_url, is_admin, membership_ok, membership_required
from i18n import t
from tg import cb_message

private_chat = F.chat.type == "private"


async def guard(event: Message | CallbackQuery, lang: str) -> bool:
    """Проверка членства. True — доступ разрешён."""
    actor = event.from_user
    if actor is None:  # в личных чатах такого не бывает
        return True
    if is_admin(actor.id):
        return True
    if not await membership_required():
        return True
    if await membership_ok(actor.id):
        return True
    url = await get_invite_url()
    kb = menus.membership_kb(lang, url)
    text = t(lang, "member.denied")
    if isinstance(event, CallbackQuery):
        await cb_message(event).answer(text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")
    return False


def main_text(lang: str) -> str:
    """Текст главного меню."""
    return t(lang, "menu.title")


async def show_main(event: Message | CallbackQuery, user: dict) -> None:
    """Показывает главное меню (правит сообщение или отправляет новое)."""
    lang = user["language"]
    kb = menus.main_menu(lang, is_admin(user["user_id"]))
    text = main_text(lang)
    if isinstance(event, CallbackQuery):
        await cb_message(event).edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")
