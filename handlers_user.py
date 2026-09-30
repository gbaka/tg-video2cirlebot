"""
Пользовательские хендлеры: меню, конвертация, подписка, настройки, язык, качество.
"""

import contextlib
import logging
import os
import tempfile
import time
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Message,
    PreCheckoutQuery,
)

import menus
from access import (
    ensure_user,
    get_invite_url,
    in_maintenance,
    is_admin,
    membership_ok,
    membership_required,
    user_lang,
    user_plan,
)
from context import get_ctx
from i18n import SUPPORTED, lang_name, t
from payments import parse_payload, send_subscription_invoice
from repositories import is_lifetime
from tg import cb_data, cb_message
from video_converter import VideoConverter

logger = logging.getLogger(__name__)
router = Router()

private_chat = F.chat.type == "private"


# ===== Вспомогательные рендеры =====

async def _guard(event: Message | CallbackQuery, lang: str) -> bool:
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


def _main_text(lang: str) -> str:
    return t(lang, "menu.title")


async def _show_main(event: Message | CallbackQuery, user: dict) -> None:
    lang = user["language"]
    kb = menus.main_menu(lang, is_admin(user["user_id"]))
    text = _main_text(lang)
    if isinstance(event, CallbackQuery):
        await cb_message(event).edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


# ===== Команды =====

@router.message(Command("start"), private_chat)
async def cmd_start(message: Message, command: CommandObject) -> None:
    user = await ensure_user(message)
    lang = await user_lang(user)
    if not await _guard(message, lang):
        return
    await _show_main(message, user)


@router.message(Command("menu"), private_chat)
async def cmd_menu(message: Message) -> None:
    user = await ensure_user(message)
    lang = await user_lang(user)
    if not await _guard(message, lang):
        return
    await _show_main(message, user)


def _help_text(lang: str, user: dict) -> str:
    """Справка; админские команды показываем только администраторам."""
    text = t(lang, "help.text")
    uid = user.get("user_id")
    if uid is not None and is_admin(uid):
        text += t(lang, "help.admin")
    return text


@router.message(Command("help"), private_chat)
async def cmd_help(message: Message) -> None:
    user = await ensure_user(message)
    lang = await user_lang(user)
    if not await _guard(message, lang):
        return
    await message.answer(
        _help_text(lang, user),
        reply_markup=menus.back_main(lang),
        parse_mode="HTML",
    )


@router.message(Command("setlanguage"), private_chat)
async def cmd_setlanguage(message: Message) -> None:
    user = await ensure_user(message)
    lang = await user_lang(user)
    await message.answer(
        t(lang, "lang.title"),
        reply_markup=menus.language_menu(lang, lang),
        parse_mode="HTML",
    )


# ===== Навигация по меню =====

@router.callback_query(F.data == "m:main")
async def cb_main(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    await _show_main(cb, user)
    await cb.answer()


@router.callback_query(F.data == "m:info")
async def cb_info(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        t(lang, "info.text"), reply_markup=menus.back_main(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "m:help")
async def cb_help(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        _help_text(lang, user), reply_markup=menus.back_main(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "m:profile")
async def cb_profile(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    plan, _ = await user_plan(user)

    today = await ctx.usage.count_user_today(user["user_id"])
    total = await ctx.usage._scalar(
        "SELECT COUNT(*) FROM usage WHERE user_id = ? AND status = 'ok'", (user["user_id"],)
    )
    if plan.code == "pro":
        quality = t(lang, "profile.quality_selectable",
                    res=plan.normalize_resolution(user.get("quality")))
    else:
        quality = t(lang, "profile.quality_fixed", res=plan.default_resolution)

    limit_str = (
        "" if plan.is_unlimited()
        else t(lang, "profile.today_limit", limit=plan.daily_limit)
    )
    plan_label = "⭐ Pro" if plan.code == "pro" else "Free"

    text = t(lang, "profile.title") + t(
        lang, "profile.body",
        user_id=user["user_id"], plan=plan_label, quality=quality,
        today=today, today_limit=limit_str, total=total,
        created=(user.get("created_at") or "")[:10],
    )
    await cb_message(cb).edit_text(text, reply_markup=menus.back_main(lang), parse_mode="HTML")
    await cb.answer()


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


# ===== Подписка =====

@router.callback_query(F.data == "m:sub")
async def cb_subscription(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    plan, sub = await user_plan(user)
    free, pro = ctx.plans.free, ctx.plans.pro

    if plan.code == "pro":
        expires = t(lang, "sub.lifetime") if is_lifetime(sub["expires_at"] if sub else None) \
            else (sub["expires_at"][:10] if sub else "—")
        text = t(lang, "sub.title") + t(
            lang, "sub.current_pro",
            maxres=max(pro.resolutions), size=pro.max_size_mb,
            expires=expires,
        )
    else:
        text = t(lang, "sub.title") + t(
            lang, "sub.current_free" if not free.is_unlimited() else "sub.current_free_unlimited",
            res=free.default_resolution, size=free.max_size_mb, limit=free.daily_limit,
            album=free.max_album,
        )

    if ctx.plans.prices:
        text += t(lang, "sub.buy_title")
    else:
        text += t(lang, "sub.no_prices")

    await cb_message(cb).edit_text(
        text,
        reply_markup=menus.subscription_menu(lang, ctx.plans.prices, plan.code == "pro"),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "s:off")
async def cb_sub_cancel(cb: CallbackQuery) -> None:
    """Экран подтверждения досрочной отмены подписки."""
    user = await ensure_user(cb)
    lang = user["language"]
    _, sub = await user_plan(user)
    if not sub:
        await cb.answer(t(lang, "sub.cancel_none"), show_alert=True)
        return
    expires = (t(lang, "sub.lifetime") if is_lifetime(sub["expires_at"])
               else sub["expires_at"][:10])
    await cb_message(cb).edit_text(
        t(lang, "sub.cancel_confirm", expires=expires),
        reply_markup=menus.sub_cancel_confirm(lang),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data == "s:off:yes")
async def cb_sub_cancel_confirm(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    cancelled = await ctx.subs.cancel(user["user_id"], reason="user")
    if not cancelled:
        await cb.answer(t(lang, "sub.cancel_none"), show_alert=True)
        return
    logger.info(
        "Подписка отменена пользователем: user=%s, оставалось до %s",
        user["user_id"], cancelled["expires_at"],
    )
    await cb_message(cb).edit_text(
        t(lang, "sub.cancel_done"),
        reply_markup=menus.back_main(lang),
        parse_mode="HTML",
    )
    await cb.answer(t(lang, "sub.cancel_done").split("\n")[0], show_alert=False)


@router.callback_query(F.data.startswith("b:"))
async def cb_buy(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    code = cb_data(cb).split(":", 1)[1]
    price = ctx.plans.price(code)
    if not price:
        await cb.answer("N/A", show_alert=True)
        return
    title = (t(lang, "sub.buy_btn_life", stars=price.stars) if price.lifetime
             else f"Pro · {price.days} дн." if lang == "ru" else f"Pro · {price.days}d")
    desc = ("Подписка Pro на видео-кружки" if lang == "ru"
            else "Pro subscription for video notes")
    await send_subscription_invoice(ctx.bot, cb.from_user.id, price, title, desc)
    await cb.answer()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment, private_chat)
async def on_successful_payment(message: Message) -> None:
    ctx = get_ctx()
    user = await ensure_user(message)
    lang = user["language"]
    sp = message.successful_payment
    if sp is None:  # сообщение с типом successful_payment всегда его содержит
        logger.warning("Платёж без данных successful_payment")
        return

    data = parse_payload(sp.invoice_payload)
    if not data:
        logger.warning("Неизвестный payload платежа: %s", sp.invoice_payload)
        await message.answer(t(lang, "sub.payment_error"), parse_mode="HTML")
        return

    # Идемпотентность по charge_id
    fresh = await ctx.payments.add(
        user_id=user["user_id"], charge_id=sp.telegram_payment_charge_id,
        plan=data["plan"], days=data["days"], stars=data["stars"],
    )
    if not fresh:
        logger.info("Повторный платёж %s — игнорируем", sp.telegram_payment_charge_id)
        await message.answer(t(lang, "sub.already_paid"), parse_mode="HTML")
        return

    expires = await ctx.subs.activate(
        user_id=user["user_id"], plan=data["plan"], days=data["days"],
        source="stars", charge_id=sp.telegram_payment_charge_id,
        lifetime=data.get("lifetime", False),
    )
    shown = t(lang, "sub.lifetime") if is_lifetime(expires) else expires[:10]
    await message.answer(
        t(lang, "sub.paid_success", expires=shown),
        reply_markup=menus.main_menu(lang, is_admin(user["user_id"])),
        parse_mode="HTML",
    )
    logger.info(
        "Оплата Stars: user=%s plan=%s days=%s stars=%s lifetime=%s",
        user["user_id"], data["plan"], data["days"], data["stars"],
        data.get("lifetime", False),
    )


# ===== Проверка подписки по кнопке =====

@router.callback_query(F.data == "c:check")
async def cb_check_membership(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    if is_admin(user["user_id"]) or await membership_ok(user["user_id"]):
        await cb.answer("✅")
        await _show_main(cb, user)
    else:
        await cb.answer("❌", show_alert=True)


# ===== Обработка видео =====

def _extract_file(message: Message):
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


def _step(lang: str, key: str, index: int, total: int) -> str:
    """Текст статуса; для альбома добавляет «(i/n)»."""
    text = t(lang, key)
    return f"{text} ({index}/{total})" if total > 1 else text


async def _convert_one(ctx, user, lang, plan, item, status, index, total) -> bool:
    """Конвертирует один файл в кружок. True — успех."""
    msg, file_id, file_name, file_size = item
    task_id = ctx.tasks.add(
        kind="convert", user_id=user["user_id"], chat_id=msg.chat.id,
        filename=file_name, size=file_size,
    )
    started = time.monotonic()
    input_path = output_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_name).suffix) as tmp:
            input_path = tmp.name
        await ctx.bot.download(file_id, destination=input_path)

        info = await VideoConverter.probe(input_path)
        if info["duration"] > plan.max_duration_sec + 1:
            await status.edit_text(t(
                lang, "conv.too_long",
                duration=int(info["duration"]), limit=plan.max_duration_sec,
            ))
            await ctx.usage.add(
                user_id=user["user_id"], status="error", plan=plan.code,
                file_size=file_size, duration=info["duration"],
                fmt=Path(file_name).suffix.lower(), error="duration_limit",
                processing_ms=int((time.monotonic() - started) * 1000),
            )
            return False

        await status.edit_text(_step(lang, "conv.converting", index, total))
        resolution = plan.normalize_resolution(user.get("quality"))
        output_path, meta = await ctx.converter.convert_to_circle(
            input_path, resolution=resolution, crf=plan.crf, preset=plan.preset
        )

        await status.edit_text(_step(lang, "conv.uploading", index, total))
        with open(output_path, "rb") as f:
            payload = BufferedInputFile(f.read(), filename="circle.mp4")
        await msg.answer_video_note(
            payload,
            duration=max(1, int(meta["duration"])),
            length=meta["width"],
        )
        await ctx.usage.add(
            user_id=user["user_id"], status="ok", plan=plan.code,
            file_size=file_size, duration=meta["duration"],
            fmt=Path(file_name).suffix.lower(),
            processing_ms=int((time.monotonic() - started) * 1000),
        )
        return True

    except TelegramBadRequest as e:
        logger.warning("TelegramBadRequest: %s", e)
        await msg.answer(t(lang, "conv.duration_hint"))
        await ctx.usage.add(
            user_id=user["user_id"], status="error", plan=plan.code, file_size=file_size,
            fmt=Path(file_name).suffix.lower(), error=str(e)[:200],
            processing_ms=int((time.monotonic() - started) * 1000),
        )
        return False
    except Exception as e:
        logger.exception("Ошибка конвертации")
        await msg.answer(t(lang, "conv.error"))
        await ctx.usage.add(
            user_id=user["user_id"], status="error", plan=plan.code, file_size=file_size,
            fmt=Path(file_name).suffix.lower(), error=str(e)[:200],
            processing_ms=int((time.monotonic() - started) * 1000),
        )
        return False
    finally:
        ctx.tasks.remove(task_id)
        for path in (input_path, output_path):
            if path and os.path.exists(path):
                with contextlib.suppress(OSError):
                    os.unlink(path)


async def _process_batch(messages: list, user, lang, plan) -> None:
    """Обрабатывает одно видео или альбом: лимиты, валидация, конвертация."""
    ctx = get_ctx()
    # Сериализуем обработку видео одного пользователя: иначе два видео,
    # отправленных подряд, могут одновременно пройти проверку лимита.
    async with ctx.locks.hold(user["user_id"]):
        await _process_batch_locked(messages, user, lang, plan)


async def _process_batch_locked(messages: list, user, lang, plan) -> None:
    ctx = get_ctx()
    pairs = [(m, f) for m in messages if (f := _extract_file(m))]
    if not pairs:
        return

    # Лимит «видео в одном сообщении»
    if not plan.album_unlimited() and len(pairs) > plan.max_album:
        await messages[0].answer(
            t(lang, "conv.album_limit", limit=plan.max_album, sent=len(pairs)),
            reply_markup=menus.main_menu(lang, is_admin(user["user_id"])),
            parse_mode="HTML",
        )
        return

    # Предварительная проверка — все проблемы показываем одним сообщением
    valid, problems = [], []
    for msg, (file_id, file_name, file_size) in pairs:
        if plan.max_size_bytes and file_size > plan.max_size_bytes:
            problems.append(t(
                lang, "conv.too_big",
                size=round(file_size / 1024 / 1024, 1), limit=plan.max_size_mb,
            ))
        elif not VideoConverter.is_supported(file_name):
            problems.append(t(lang, "conv.unsupported", ext=Path(file_name).suffix))
        else:
            valid.append((msg, file_id, file_name, file_size))

    if problems:
        await messages[0].answer("\n\n".join(problems), parse_mode="HTML")
    if not valid:
        return

    status = await messages[0].answer(_step(lang, "conv.downloading", 1, len(valid)))
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
                        reply_markup=menus.main_menu(lang, is_admin(user["user_id"])),
                        parse_mode="HTML",
                    )
                    return
            if index > 1:
                await status.edit_text(_step(lang, "conv.downloading", index, len(valid)))
            await _convert_one(ctx, user, lang, plan, item, status, index, len(valid))
    finally:
        if not keep_status:
            with contextlib.suppress(TelegramBadRequest):
                await status.delete()


@router.message(F.video | F.video_note | F.document, private_chat)
async def handle_video(message: Message) -> None:
    ctx = get_ctx()
    user = await ensure_user(message)
    lang = user["language"]

    # Обслуживание (кроме админов)
    if await in_maintenance() and not is_admin(user["user_id"]):
        await message.answer(t(lang, "conv.maintenance"), parse_mode="HTML")
        return

    if not await _guard(message, lang):
        return

    plan, _ = await user_plan(user)

    # Альбом: Telegram присылает каждое видео отдельным сообщением с общим
    # media_group_id — копим их и обрабатываем пачкой.
    if message.media_group_id:
        ctx.albums.add(
            message.media_group_id,
            message,
            lambda msgs: _process_batch(msgs, user, lang, plan),
        )
        return

    await _process_batch([message], user, lang, plan)


@router.message(private_chat)
async def handle_other(message: Message) -> None:
    user = await ensure_user(message)
    lang = user["language"]
    if not await _guard(message, lang):
        return
    await message.answer(
        t(lang, "conv.send_video"),
        reply_markup=menus.main_menu(lang, is_admin(user["user_id"])),
        parse_mode="HTML",
    )
