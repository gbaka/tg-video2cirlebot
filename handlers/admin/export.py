"""
Экспорт пользователей в CSV
"""

import contextlib
import csv
import logging
import os
import tempfile
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from access import ensure_user
from context import get_ctx
from handlers.admin.shared import (
    EXPORT_PAGE,
    admin_guard,
    private_chat,
)
from i18n import t
from repositories import is_lifetime
from tg import cb_message

logger = logging.getLogger(__name__)
router = Router(name="admin.export")

CSV_HEADERS = [
    "user_id", "username", "first_name", "language", "quality", "plan",
    "expires_at", "created_at", "last_seen", "conversions_ok", "conversions_error",
]


def _csv_expires(value: str | None) -> str:
    if not value:
        return ""
    return "lifetime" if is_lifetime(value) else value[:10]


async def _write_csv() -> tuple[str, int]:
    """Выгружает всех пользователей в CSV. Возвращает (путь, число записей)."""
    ctx = get_ctx()
    path = os.path.join(
        tempfile.gettempdir(), f"users_{datetime.now(UTC):%Y%m%d_%H%M%S}.csv"
    )
    count = 0
    # utf-8-sig + ';' — чтобы Excel корректно открыл русский текст
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(CSV_HEADERS)
        offset = 0
        while True:
            rows = await ctx.users.export_page(offset, EXPORT_PAGE)
            if not rows:
                break
            for r in rows:
                writer.writerow([
                    r["user_id"], r.get("username") or "", r.get("first_name") or "",
                    r.get("language") or "", r.get("quality") or "",
                    r.get("active_plan") or "free",
                    _csv_expires(r.get("expires_at")),
                    (r.get("created_at") or "")[:19], (r.get("last_seen") or "")[:19],
                    r.get("conv_ok") or 0, r.get("conv_err") or 0,
                ])
            count += len(rows)
            offset += EXPORT_PAGE
            if len(rows) < EXPORT_PAGE:
                break
    return path, count


async def _send_export(message: Message, lang: str) -> None:
    status = await message.answer(t(lang, "export.generating"))
    path, count = await _write_csv()
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
                lang, "export.caption",
                count=count, date=f"{datetime.now(UTC):%Y-%m-%d %H:%M}",
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
