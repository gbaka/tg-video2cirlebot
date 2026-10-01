"""
Конвертация видео в «кружки»: проверка лимитов и сам пайплайн.

Модуль не знает про меню и роутеры aiogram — клавиатуру для сообщений о
лимите хендлер передаёт фабрикой `menu` (строится только когда нужна).
"""

import asyncio
import contextlib
import html
import logging
import math
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message

from context import AppContext
from conversion_queue import QueueFull
from i18n import t
from plans import Plan
from repositories.conversion_cache import make_cache_key
from tempfiles import TempFiles
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


def _usable_cache_record(cached: dict[str, Any] | None) -> bool:
    if not cached or not isinstance(cached.get("file_id"), str) or not cached["file_id"]:
        return False
    try:
        size = float(cached["source_size"])
        source = float(cached["source_duration"])
        output = float(cached["output_duration"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(size)
        and size >= 0
        and math.isfinite(source)
        and source > 0
        and math.isfinite(output)
        and output > 0
    )


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
    """One isolated, deadline-bounded job; terminal errors survive progress cleanup."""
    msg, file_id, file_name, file_size = item
    task_id = ctx.tasks.add(
        kind="convert",
        user_id=user["user_id"],
        chat_id=msg.chat.id,
        filename=file_name,
        size=file_size,
    )
    started = time.monotonic()
    accounted = False
    processing = getattr(ctx.config, "processing", None)
    manager = getattr(ctx, "tempfiles", None) or TempFiles(
        getattr(processing, "temp_dir", tempfile.gettempdir())
    )
    probe_timeout = getattr(processing, "probe_timeout_sec", 15)
    encode_timeout = getattr(processing, "encode_timeout_sec", 120)

    async def record(state: str, *, duration: float = 0, error: str | None = None) -> None:
        await ctx.usage.add(
            user_id=user["user_id"],
            status=state,
            plan=plan.code,
            file_size=file_size,
            duration=duration,
            fmt=Path(file_name).suffix.lower(),
            error=error or "",
            processing_ms=int((time.monotonic() - started) * 1000),
        )

    cache = getattr(ctx, "conversion_cache", None)
    media = msg.video or msg.video_note or msg.document
    unique_id = getattr(media, "file_unique_id", None)
    try:
        try:
            trim_start = float(user.get("trim_start") or 0)
            raw_selected = user.get("trim_duration")
            trim_duration = float(raw_selected) if raw_selected is not None else None
            if (
                not math.isfinite(trim_start)
                or trim_start < 0
                or (
                    trim_duration is not None
                    and (not math.isfinite(trim_duration) or trim_duration <= 0)
                )
            ):
                raise ValueError("invalid trim")
        except (ValueError, TypeError, OverflowError):
            await msg.answer(t(lang, "conv.error"))
            await record("error", error="invalid_trim")
            return False
        options = {
            "resolution": plan.normalize_resolution(user.get("quality")),
            "crf": plan.crf,
            "preset": plan.preset,
            "crop_mode": user.get("crop_mode", "crop"),
            "trim_start": trim_start,
            "trim_duration": trim_duration,
        }
        cache_key = make_cache_key(unique_id, **options) if cache and unique_id else None
        api = getattr(ctx.config, "api", None)
        limit = api.input_limit_bytes(plan.max_size_bytes) if api else plan.max_size_bytes
        selected = options["trim_duration"]
        if selected is not None and float(selected) > plan.max_duration_sec:
            await msg.answer(
                t(lang, "conv.too_long", duration=int(selected), limit=plan.max_duration_sec)
            )
            await record("error", duration=float(selected), error="duration_limit")
            return False
        if cache is not None and cache_key:
            cached = await cache.get_record(cache_key)
            if _usable_cache_record(cached):
                assert cached is not None
                source_duration = float(cached["source_duration"])
                if limit and max(file_size, int(cached["source_size"])) > limit:
                    await msg.answer(
                        t(
                            lang,
                            "conv.too_big",
                            size=round(int(cached["source_size"]) / 1024 / 1024, 1),
                            limit=limit / 1024 / 1024,
                        )
                    )
                    await record("error", error="size_limit")
                    return False
                if trim_start >= source_duration:
                    await msg.answer(t(lang, "conv.error"))
                    await record("error", error="invalid_trim")
                    return False
                if selected is None and source_duration > plan.max_duration_sec:
                    await msg.answer(
                        t(
                            lang,
                            "conv.too_long",
                            duration=int(source_duration),
                            limit=plan.max_duration_sec,
                        )
                    )
                    await record("error", duration=source_duration, error="duration_limit")
                    return False
                if float(cached["output_duration"]) <= plan.max_duration_sec:
                    try:
                        await asyncio.wait_for(
                            msg.answer_video_note(cached["file_id"]), encode_timeout
                        )
                    except (TelegramBadRequest, ValueError):
                        with contextlib.suppress(Exception):
                            await cache.delete(cache_key)
                    else:
                        await record("ok", duration=float(cached["output_duration"]))
                        return True
        async with manager.job() as directory:
            input_path = str(directory / ("input" + Path(file_name).suffix.lower()))
            output_path = str(directory / "circle.mp4")
            await asyncio.wait_for(
                ctx.bot.download(file_id, destination=input_path), timeout=encode_timeout
            )
            api = getattr(ctx.config, "api", None)
            limit = api.input_limit_bytes(plan.max_size_bytes) if api else plan.max_size_bytes
            actual_size = Path(input_path).stat().st_size
            if limit and actual_size > limit:
                await msg.answer(
                    t(
                        lang,
                        "conv.too_big",
                        size=round(actual_size / 1024 / 1024, 1),
                        limit=limit / 1024 / 1024,
                    )
                )
                await record("error", error="size_limit")
                return False
            info = await asyncio.wait_for(VideoConverter.probe(input_path), probe_timeout)
            if trim_start >= info["duration"]:
                await msg.answer(t(lang, "conv.error"))
                await record("error", error="invalid_trim")
                return False
            if (trim_duration is None and info["duration"] > plan.max_duration_sec) or (
                trim_duration is not None and float(trim_duration) > plan.max_duration_sec
            ):
                await msg.answer(
                    t(
                        lang,
                        "conv.too_long",
                        duration=int(info["duration"]),
                        limit=plan.max_duration_sec,
                    )
                )
                await record("error", duration=info["duration"], error="duration_limit")
                return False
            last_percent = -1

            async def progress(percent: int) -> None:
                nonlocal last_percent
                if percent > last_percent and (
                    percent == 100 or percent // 10 > last_percent // 10
                ):
                    last_percent = percent
                    with contextlib.suppress(TelegramBadRequest):
                        await status.edit_text(
                            step_text(lang, "conv.converting", index, total) + f" {percent}%"
                        )

            await status.edit_text(step_text(lang, "conv.converting", index, total))
            output_path, meta = await asyncio.wait_for(
                ctx.converter.convert_to_circle(
                    input_path,
                    resolution=plan.normalize_resolution(user.get("quality")),
                    crf=plan.crf,
                    preset=plan.preset,
                    output_path=output_path,
                    crop_mode=user.get("crop_mode", "crop"),
                    trim_start=trim_start,
                    trim_duration=trim_duration,
                    progress_callback=progress,
                ),
                encode_timeout,
            )
            await status.edit_text(step_text(lang, "conv.uploading", index, total))
            sent = await asyncio.wait_for(
                msg.answer_video_note(
                    FSInputFile(output_path, filename="circle.mp4"),
                    duration=max(1, int(meta["duration"])),
                    length=meta["width"],
                ),
                encode_timeout,
            )
            await record("ok", duration=meta["duration"])
            accounted = True
            if cache is not None and cache_key and sent.video_note:
                try:
                    await cache.put(
                        cache_key,
                        sent.video_note.file_id,
                        source_size=actual_size,
                        source_duration=info["duration"],
                        output_duration=meta["duration"],
                    )
                except Exception:
                    logger.warning("Conversion delivered; cache write failed", exc_info=True)
            return True
    except asyncio.CancelledError:
        if not accounted:
            await record("error", error="cancelled")
        raise
    except Exception as exc:
        logger.exception("Conversion failed")
        code = (
            "timeout"
            if isinstance(exc, TimeoutError)
            else "telegram_bad_request"
            if isinstance(exc, TelegramBadRequest)
            else "invalid_media"
            if isinstance(exc, ValueError)
            else "encode_failed"
            if isinstance(exc, RuntimeError)
            else "conversion_failed"
        )
        await record("error", error=code)
        await msg.answer(t(lang, "conv.error"))
        return False
    finally:
        ctx.tasks.remove(task_id)


async def process_batch(
    ctx: AppContext,
    user: dict[str, Any],
    lang: str,
    plan: Plan,
    messages: list[Message],
    menu: Callable[[], InlineKeyboardMarkup] | None = None,
    *,
    recheck: Callable[[list[Message]], Awaitable[tuple[dict[str, Any], str, Plan] | None]]
    | None = None,
) -> None:
    """
    Обрабатывает одно видео или альбом: лимиты, валидация, конвертация.

    Обработка видео одного пользователя сериализуется: иначе два видео,
    отправленных подряд, могут одновременно пройти проверку дневного лимита.
    """
    if not messages:
        return

    async def work() -> None:
        async with ctx.locks.hold(user["user_id"]):
            fresh = await recheck(messages) if recheck else (user, lang, plan)
            if fresh is not None:
                await process_batch_locked(ctx, *fresh, messages, menu)

    queue = getattr(ctx, "conversion_queue", None)
    if queue is None:
        await work()
        return
    try:
        job = queue.submit(work)
    except QueueFull:
        await messages[0].answer(t(lang, "conv.queue_full"))
        return
    except RuntimeError:
        await messages[0].answer(t(lang, "conv.queue_closed"))
        return
    notice = None
    try:
        if job.position:
            notice = await messages[0].answer(t(lang, "conv.queued", position=job.position))
        await job.wait()
    except BaseException:
        # Cancelled or failed before wait(): the accepted job must not keep running.
        job.cancel()
        if job.task is not None:
            await asyncio.gather(job.task, return_exceptions=True)
        raise
    finally:
        if notice:
            with contextlib.suppress(TelegramBadRequest):
                await notice.delete()


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
    api = getattr(ctx.config, "api", None)
    input_limit = api.input_limit_bytes(plan.max_size_bytes) if api else plan.max_size_bytes
    for msg, (file_id, file_name, file_size) in pairs:
        if input_limit and file_size > input_limit:
            problems.append(
                t(
                    lang,
                    "conv.too_big",
                    size=round(file_size / 1024 / 1024, 1),
                    limit=input_limit // 1024 // 1024,
                )
            )
        elif not VideoConverter.is_supported(file_name):
            problems.append(t(lang, "conv.unsupported", ext=html.escape(Path(file_name).suffix)))
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
        # Do not start network cleanup once this task is being cancelled: shutdown
        # awaits these tasks, and a stalled delete would blow the grace period.
        current = asyncio.current_task()
        if not keep_status and not (current is not None and current.cancelling()):
            with contextlib.suppress(TelegramBadRequest):
                await status.delete()
