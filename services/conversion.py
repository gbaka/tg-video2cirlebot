"""
Конвертация видео в «кружки»: проверка лимитов и сам пайплайн.

Модуль не знает про меню и роутеры aiogram — клавиатуру для сообщений о
лимите хендлер передаёт фабрикой `menu` (строится только когда нужна).
"""

import contextlib
import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from context import AppContext
from i18n import t
from plans import Plan
from video_converter import VideoConverter

logger = logging.getLogger(__name__)

#: (сообщение, file_id, имя файла, размер)
Item = tuple[Message, str, str, int]


def extract_file(message: Message) -> tuple[str, str, int] | None:
    """(file_id, file_name, file_size) для видео-сообщения, иначе None."""
    if message.video:
        return (
            message.video.file_id,
            message.video.file_name or f"video_{message.video.file_unique_id}.mp4",
            message.video.file_size or 0,
        )
    if message.video_note:
        return (
            message.video_note.file_id,
            f"circle_{message.video_note.file_unique_id}.mp4",
            message.video_note.file_size or 0,
        )
    if message.document and (message.document.mime_type or "").startswith("video/"):
        return (
            message.document.file_id,
            message.document.file_name or f"video_{message.document.file_unique_id}",
            message.document.file_size or 0,
        )
    return None


def step_text(lang: str, key: str, index: int, total: int) -> str:
    """Текст статуса; для альбома добавляет «(i/n)»."""
    text = t(lang, key)
    return f"{text} ({index}/{total})" if total > 1 else text


async def convert_one(
    ctx: AppContext,
    user: dict[str, Any],
    lang: str,
    plan: Plan,
    item: Item,
    status: Message,
    index: int,
    total: int,
) -> bool:
    """Конвертирует один файл в кружок. True — успех."""
    msg, file_id, file_name, file_size = item
    task_id = ctx.tasks.add(
        kind="convert",
        user_id=user["user_id"],
        chat_id=msg.chat.id,
        filename=file_name,
        size=file_size,
    )
    started = time.monotonic()
    input_path = output_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_name).suffix) as tmp:
            input_path = tmp.name
        await ctx.bot.download(file_id, destination=input_path)

        info = await VideoConverter.probe(input_path)
        if info["duration"] > plan.max_duration_sec + 1:
            await status.edit_text(
                t(
                    lang,
                    "conv.too_long",
                    duration=int(info["duration"]),
                    limit=plan.max_duration_sec,
                )
            )
            await ctx.usage.add(
                user_id=user["user_id"],
                status="error",
                plan=plan.code,
                file_size=file_size,
                duration=info["duration"],
                fmt=Path(file_name).suffix.lower(),
                error="duration_limit",
                processing_ms=int((time.monotonic() - started) * 1000),
            )
            return False

        await status.edit_text(step_text(lang, "conv.converting", index, total))
        resolution = plan.normalize_resolution(user.get("quality"))
        output_path, meta = await ctx.converter.convert_to_circle(
            input_path, resolution=resolution, crf=plan.crf, preset=plan.preset
        )

        await status.edit_text(step_text(lang, "conv.uploading", index, total))
        with open(output_path, "rb") as f:
            payload = BufferedInputFile(f.read(), filename="circle.mp4")
        await msg.answer_video_note(
            payload,
            duration=max(1, int(meta["duration"])),
            length=meta["width"],
        )
        await ctx.usage.add(
            user_id=user["user_id"],
            status="ok",
            plan=plan.code,
            file_size=file_size,
            duration=meta["duration"],
            fmt=Path(file_name).suffix.lower(),
            processing_ms=int((time.monotonic() - started) * 1000),
        )
        return True

    except TelegramBadRequest as e:
        logger.warning("TelegramBadRequest: %s", e)
        await msg.answer(t(lang, "conv.duration_hint"))
        await ctx.usage.add(
            user_id=user["user_id"],
            status="error",
            plan=plan.code,
            file_size=file_size,
            fmt=Path(file_name).suffix.lower(),
            error=str(e)[:200],
            processing_ms=int((time.monotonic() - started) * 1000),
        )
        return False
    except Exception as e:
        logger.exception("Ошибка конвертации")
        await msg.answer(t(lang, "conv.error"))
        await ctx.usage.add(
            user_id=user["user_id"],
            status="error",
            plan=plan.code,
            file_size=file_size,
            fmt=Path(file_name).suffix.lower(),
            error=str(e)[:200],
            processing_ms=int((time.monotonic() - started) * 1000),
        )
        return False
    finally:
        ctx.tasks.remove(task_id)
        for path in (input_path, output_path):
            if path and os.path.exists(path):
                with contextlib.suppress(OSError):
                    os.unlink(path)


async def process_batch(
    ctx: AppContext,
    user: dict[str, Any],
    lang: str,
    plan: Plan,
    messages: list[Message],
    menu: Callable[[], InlineKeyboardMarkup] | None = None,
) -> None:
    """
    Обрабатывает одно видео или альбом: лимиты, валидация, конвертация.

    Обработка видео одного пользователя сериализуется: иначе два видео,
    отправленных подряд, могут одновременно пройти проверку дневного лимита.
    """
    async with ctx.locks.hold(user["user_id"]):
        await process_batch_locked(ctx, user, lang, plan, messages, menu)


async def process_batch_locked(
    ctx: AppContext,
    user: dict[str, Any],
    lang: str,
    plan: Plan,
    messages: list[Message],
    menu: Callable[[], InlineKeyboardMarkup] | None = None,
) -> None:
    """Тело обработки пачки; вызывать только под `ctx.locks.hold`."""
    pairs = [(m, f) for m in messages if (f := extract_file(m))]
    if not pairs:
        return

    # Лимит «видео в одном сообщении»
    if not plan.album_unlimited() and len(pairs) > plan.max_album:
        await messages[0].answer(
            t(lang, "conv.album_limit", limit=plan.max_album, sent=len(pairs)),
            reply_markup=menu() if menu else None,
            parse_mode="HTML",
        )
        return

    # Предварительная проверка — все проблемы показываем одним сообщением
    valid: list[Item] = []
    problems: list[str] = []
    for msg, (file_id, file_name, file_size) in pairs:
        if plan.max_size_bytes and file_size > plan.max_size_bytes:
            problems.append(
                t(
                    lang,
                    "conv.too_big",
                    size=round(file_size / 1024 / 1024, 1),
                    limit=plan.max_size_mb,
                )
            )
        elif not VideoConverter.is_supported(file_name):
            problems.append(t(lang, "conv.unsupported", ext=Path(file_name).suffix))
        else:
            valid.append((msg, file_id, file_name, file_size))

    if problems:
        await messages[0].answer("\n\n".join(problems), parse_mode="HTML")
    if not valid:
        return

    status = await messages[0].answer(step_text(lang, "conv.downloading", 1, len(valid)))
    # Если мы оставляем сообщение пользователю (лимит исчерпан) — не удаляем его
    keep_status = False
    try:
        for index, item in enumerate(valid, 1):
            if not plan.is_unlimited():
                used = await ctx.usage.count_user_today(user["user_id"])
                if used >= plan.daily_limit:
                    keep_status = True
                    await status.edit_text(
                        t(lang, "conv.daily_limit", limit=plan.daily_limit),
                        reply_markup=menu() if menu else None,
                        parse_mode="HTML",
                    )
                    return
            if index > 1:
                await status.edit_text(step_text(lang, "conv.downloading", index, len(valid)))
            await convert_one(ctx, user, lang, plan, item, status, index, len(valid))
    finally:
        if not keep_status:
            with contextlib.suppress(TelegramBadRequest):
                await status.delete()
