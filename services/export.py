"""
Выгрузка пользователей в CSV.

Сервис создаёт файл и возвращает путь; отправку файла в чат делает хендлер,
поэтому здесь нет ни объектов aiogram, ни меню.
"""

import csv
import logging
import os
import tempfile
from datetime import UTC, datetime

from context import AppContext
from repositories import is_lifetime

logger = logging.getLogger(__name__)

#: Сколько записей запрашивать за раз (потоковая выгрузка без загрузки всего в память)
EXPORT_PAGE = 500

CSV_HEADERS = [
    "user_id", "username", "first_name", "language", "quality", "plan",
    "expires_at", "created_at", "last_seen", "conversions_ok", "conversions_error",
]


def csv_expires(value: str | None) -> str:
    """Срок действия для CSV: 'lifetime' либо дата без времени."""
    if not value:
        return ""
    return "lifetime" if is_lifetime(value) else value[:10]


async def write_users_csv(ctx: AppContext, page_size: int = EXPORT_PAGE) -> tuple[str, int]:
    """Выгружает всех пользователей в CSV. Возвращает (путь, число записей)."""
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
            rows = await ctx.users.export_page(offset, page_size)
            if not rows:
                break
            for r in rows:
                writer.writerow([
                    r["user_id"], r.get("username") or "", r.get("first_name") or "",
                    r.get("language") or "", r.get("quality") or "",
                    r.get("active_plan") or "free",
                    csv_expires(r.get("expires_at")),
                    (r.get("created_at") or "")[:19], (r.get("last_seen") or "")[:19],
                    r.get("conv_ok") or 0, r.get("conv_err") or 0,
                ])
            count += len(rows)
            offset += page_size
            if len(rows) < page_size:
                break
    return path, count
