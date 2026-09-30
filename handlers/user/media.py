"""
Приём видео и запуск конвертации.

Вся логика лимитов и кодирования — в `services.conversion`; здесь только
разбор события, проверки доступа и передача меню для сообщений о лимите.
"""

import logging

from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup, Message

import menus
from access import ensure_user, in_maintenance, is_admin, user_plan
from context import get_ctx
from handlers.user.shared import guard, private_chat
from i18n import t
from services.conversion import process_batch

logger = logging.getLogger(__name__)
router = Router(name="user.media")


@router.message(F.video | F.video_note | F.document, private_chat)
async def handle_video(message: Message) -> None:
    ctx = get_ctx()
    user = await ensure_user(message)
    lang = user["language"]

    # Обслуживание (кроме админов)
    if await in_maintenance() and not is_admin(user["user_id"]):
        await message.answer(t(lang, "conv.maintenance"), parse_mode="HTML")
        return

    if not await guard(message, lang):
        return

    plan, _ = await user_plan(user)

    def menu() -> InlineKeyboardMarkup:
        return menus.main_menu(lang, is_admin(user["user_id"]))

    # Альбом: Telegram присылает каждое видео отдельным сообщением с общим
    # media_group_id — копим их и обрабатываем пачкой.
    if message.media_group_id:
        ctx.albums.add(
            message.media_group_id,
            message,
            lambda msgs: process_batch(ctx, user, lang, plan, msgs, menu),
        )
        return

    await process_batch(ctx, user, lang, plan, [message], menu)


@router.message(private_chat)
async def handle_other(message: Message) -> None:
    """Любое прочее сообщение в личке — подсказка, что бот умеет."""
    user = await ensure_user(message)
    lang = user["language"]
    if not await guard(message, lang):
        return
    await message.answer(
        t(lang, "conv.send_video"),
        reply_markup=menus.main_menu(lang, is_admin(user["user_id"])),
        parse_mode="HTML",
    )
