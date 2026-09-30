"""
Экспорт пользователей в CSV.

Файл готовит `services.export`, здесь — только отправка его админу.
"""

import contextlib
import logging
import os
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from access import ensure_user
from context import get_ctx
from handlers.admin.shared import admin_guard, private_chat
from i18n import t
from services.export import write_users_csv
from tg import cb_message

logger = logging.getLogger(__name__)
router = Router(name="admin.export")


async def _send_export(message: Message, lang: str) -> None:
    """Готовит выгрузку, отправляет её файлом и удаляет с диска."""
    status = await message.answer(t(lang, "export.generating"))
    path, count = await write_users_csv(get_ctx())
    if not count:
        await status.edit_text(t(lang, "export.empty"))
        with contextlib.suppress(OSError):
            os.unlink(path)
        return

    try:
        await status.delete()
        await message.answer_document(
            FSInputFile(path, filename="users.csv"),
            caption=t(
                lang,
                "export.caption",
                count=count,
                date=f"{datetime.now(UTC):%Y-%m-%d %H:%M}",
            ),
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


@router.callback_query(F.data == "a:export")
async def cb_export(cb: CallbackQuery) -> None:
    if not await admin_guard(cb):
        return
    admin = await ensure_user(cb)
    await cb.answer()
    await _send_export(cb_message(cb), admin["language"])


@router.message(Command("exportusers"), private_chat)
async def cmd_export_users(message: Message) -> None:
    if not await admin_guard(message):
        return
    admin = await ensure_user(message)
    await _send_export(message, admin["language"])
