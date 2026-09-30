"""
Админские хендлеры: статистика, задачи, пользователи, настройки, канал.
"""

import csv
import logging
import os
import tempfile
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

import menus
from access import ensure_user, is_admin, user_lang
from context import get_ctx
from i18n import lang_name, t
from repositories import _ago, is_lifetime

logger = logging.getLogger(__name__)
router = Router()

private_chat = F.chat.type == "private"

USERS_PAGE_SIZE = 10
EXPORT_PAGE = 500
GIFT_DEFAULT_DAYS = 30
LIFETIME_WORDS = {"life", "lifetime", "forever", "навсегда", "вечно"}


class AdminUsers(StatesGroup):
    """Ввод поискового запроса по пользователям."""
    search = State()


async def _admin_guard(event: Message | CallbackQuery) -> bool:
    if not is_admin(event.from_user.id):
        if isinstance(event, CallbackQuery):
            await event.answer(t("ru", "admin.denied"), show_alert=True)
        else:
            await event.answer(t("ru", "admin.denied"))
        return False
    return True


# ===== Вход в админ-меню =====

@router.callback_query(F.data == "m:admin")
async def cb_admin(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    await cb.message.edit_text(
        t(lang, "admin.title"), reply_markup=menus.admin_menu(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.message(Command("admin"), private_chat)
async def cmd_admin(message: Message) -> None:
    if not await _admin_guard(message):
        return
    user = await ensure_user(message)
    lang = user["language"]
    await message.answer(
        t(lang, "admin.title"), reply_markup=menus.admin_menu(lang), parse_mode="HTML"
    )


# ===== Статистика =====

async def _render_stats(lang: str) -> str:
    ctx = get_ctx()
    users_total = await ctx.users.count_all()
    users_new_day = await ctx.users.count_since(_ago(days=1))
    users_new_week = await ctx.users.count_since(_ago(days=7))
    users_active_day = await ctx.users.count_active_since(_ago(days=1))

    conv_total = await ctx.usage.count_all()
    conv_day = await ctx.usage.count_since(_ago(days=1))
    conv_week = await ctx.usage.count_since(_ago(days=7))
    errors_week = await ctx.usage.count_since(_ago(days=7), status="error")
    avg_ms = await ctx.usage.avg_processing_ms(_ago(days=7))
    total_bytes = await ctx.usage.total_bytes(_ago(days=7))

    subs_active = await ctx.subs.count_active()
    pay_count = await ctx.payments.count()
    stars_total = await ctx.payments.total_stars()

    top = await ctx.usage.top_formats(_ago(days=30), limit=5)
    top_str = "\n".join(f"• {fmt or '—'}: {cnt}" for fmt, cnt in top) or "—"

    daily = await ctx.usage.daily_counts(days=7)
    daily_str = "\n".join(f"• {d}: {c}" for d, c in daily) or "—"

    return t(lang, "stats.title") + t(
        lang, "stats.body",
        users_total=users_total, users_new_day=users_new_day, users_new_week=users_new_week,
        users_active_day=users_active_day,
        conv_total=conv_total, conv_day=conv_day, conv_week=conv_week,
        errors_week=errors_week, avg_time=round(avg_ms / 1000, 1),
        total_mb=round(total_bytes / 1024 / 1024, 1),
        subs_active=subs_active, pay_count=pay_count, stars_total=stars_total,
        top_formats=top_str, daily=daily_str,
    )


@router.callback_query(F.data == "a:stats")
async def cb_stats(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    text = await _render_stats(lang)
    await cb.message.edit_text(text, reply_markup=menus.admin_menu(lang), parse_mode="HTML")
    await cb.answer()


@router.message(Command("stats"), private_chat)
async def cmd_stats(message: Message) -> None:
    if not await _admin_guard(message):
        return
    user = await ensure_user(message)
    lang = user["language"]
    await message.answer(await _render_stats(lang), parse_mode="HTML")


# ===== Задачи =====

async def _render_tasks(lang: str) -> str:
    ctx = get_ctx()
    active = ctx.tasks.all()
    text = t(lang, "tasks.title") + t(lang, "tasks.active_title", n=len(active))
    if not active:
        text += t(lang, "tasks.none")
    else:
        for task in active:
            text += t(
                lang, "tasks.active_item",
                user=task.user_id, name=task.filename[:40],
                size=round(task.size / 1024 / 1024, 1), elapsed=ctx.tasks.elapsed(task),
            )
    text += t(lang, "tasks.jobs_title")
    for job in ctx.jobs.jobs:
        last = job.last_run.strftime("%H:%M:%S") if job.last_run else t(lang, "tasks.job_never")
        text += t(lang, "tasks.job_item", name=job.name, runs=job.runs, last=last)
    pending = ctx.albums.pending()
    if pending:
        text += t(lang, "tasks.albums", count=pending)
    return text


@router.callback_query(F.data == "a:tasks")
async def cb_tasks(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    await cb.message.edit_text(
        await _render_tasks(lang), reply_markup=menus.admin_menu(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.message(Command("tasks"), private_chat)
async def cmd_tasks(message: Message) -> None:
    if not await _admin_guard(message):
        return
    user = await ensure_user(message)
    await message.answer(await _render_tasks(user["language"]), parse_mode="HTML")


# ===== Пользователи =====

async def _render_users(lang: str, offset: int, query: str = "") -> tuple[str, object]:
    ctx = get_ctx()
    users, total = await ctx.users.search_page(offset, USERS_PAGE_SIZE, query)

    text = t(lang, "users.title") + t(
        lang, "users.body",
        total=total,
        new_day=await ctx.users.count_since(_ago(days=1)),
    )
    if query:
        text += t(lang, "users.for_query", query=escape(query))

    if not users:
        text += t(lang, "users.nothing")
    else:
        for u in users:
            plan = "⭐ Pro" if u.get("active_plan") else "Free"
            text += t(
                lang, "users.item",
                user_id=u["user_id"],
                name=escape(u.get("first_name") or u.get("username") or "—"),
                plan=plan,
                created=(u.get("created_at") or "")[:10],
            )
        shown_from = offset + 1
        shown_to = offset + len(users)
        text += t(lang, "users.showing", start=shown_from, end=shown_to, total=total)

    markup = menus.users_page_menu(lang, offset, USERS_PAGE_SIZE, total, query, users)
    return text, markup


@router.callback_query(F.data == "a:users")
async def cb_users(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard(cb):
        return
    await state.clear()
    user = await ensure_user(cb)
    lang = user["language"]
    text, markup = await _render_users(lang, 0)
    await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("u:p:"))
async def cb_users_page(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    try:
        offset = max(0, int(cb.data.split(":")[2]))
    except (IndexError, ValueError):
        await cb.answer()
        return
    data = await state.get_data()
    text, markup = await _render_users(lang, offset, data.get("query", ""))
    await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "u:noop")
async def cb_users_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data == "u:clr")
async def cb_users_clear(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard(cb):
        return
    await state.clear()
    user = await ensure_user(cb)
    text, markup = await _render_users(user["language"], 0)
    await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "u:srch")
async def cb_users_search(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    await state.set_state(AdminUsers.search)
    await cb.message.edit_text(
        t(lang, "users.search_prompt"),
        reply_markup=menus.users_search_cancel(lang),
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(AdminUsers.search, private_chat)
async def msg_users_search(message: Message, state: FSMContext) -> None:
    if not await _admin_guard(message):
        return
    user = await ensure_user(message)
    lang = user["language"]
    query = (message.text or "").strip()
    await state.update_data(query=query)
    await state.set_state(None)
    text, markup = await _render_users(lang, 0, query)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.message(Command("finduser"), private_chat)
async def cmd_find_user(message: Message, command: CommandObject) -> None:
    if not await _admin_guard(message):
        return
    user = await ensure_user(message)
    lang = user["language"]
    query = (command.args or "").strip()
    text, markup = await _render_users(lang, 0, query)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


# ===== Настройки бота =====

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
    if not await _admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    text, maint, memb = await _render_botset(lang)
    await cb.message.edit_text(
        text, reply_markup=menus.bot_settings_menu(lang, maint, memb), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bs:"))
async def cb_toggle_setting(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    key = cb.data.split(":", 1)[1]
    mapping = {"maintenance": ("maintenance", False), "membership": ("membership_check", True)}
    if key not in mapping:
        await cb.answer()
        return
    setting_key, default = mapping[key]
    current = await ctx.settings.get_bool(setting_key, default)
    await ctx.settings.set(setting_key, "false" if current else "true")
    text, maint, memb = await _render_botset(lang)
    await cb.message.edit_text(
        text, reply_markup=menus.bot_settings_menu(lang, maint, memb), parse_mode="HTML"
    )
    await cb.answer("✅")


# ===== Канал =====

@router.callback_query(F.data == "a:chan")
async def cb_channel(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    link = await ctx.channel.get_link()
    text = t(lang, "channel.title")
    text += t(lang, "channel.current", link=link) if link else t(lang, "channel.not_set")
    text += t(lang, "channel.usage")
    await cb.message.edit_text(
        text, reply_markup=menus.channel_menu(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.message(Command("setchannel"), private_chat)
async def cmd_set_channel(message: Message, command: CommandObject) -> None:
    if not await _admin_guard(message):
        return
    ctx = get_ctx()
    user = await ensure_user(message)
    lang = user["language"]
    args = (command.args or "").strip()
    if not args:
        link = await ctx.channel.get_link()
        text = t(lang, "channel.title")
        text += t(lang, "channel.current", link=link) if link else t(lang, "channel.not_set")
        text += t(lang, "channel.usage")
        await message.answer(text, parse_mode="HTML")
        return

    checker = ctx.channel_checker
    old = checker.channel_link
    checker.set_link(args)
    try:
        chat = await ctx.bot.get_chat(checker.chat_id)
        await ctx.channel.set_link(args)
        await message.answer(
            f"✅ <b>{chat.title}</b>\n🔗 <code>{args}</code>\n🔐 {chat.type}\n"
            f"👥 {getattr(chat, 'participants_count', '?')}",
            parse_mode="HTML",
        )
    except Exception as e:
        checker.set_link(old)
        await message.answer(
            f"❌ Не удалось получить доступ к каналу/чату.\n<code>{e}</code>", parse_mode="HTML"
        )


@router.message(Command("channel"), private_chat)
async def cmd_show_channel(message: Message) -> None:
    if not await _admin_guard(message):
        return
    ctx = get_ctx()
    user = await ensure_user(message)
    lang = user["language"]
    link = await ctx.channel.get_link()
    if not link:
        await message.answer(t(lang, "channel.not_set"), parse_mode="HTML")
        return
    try:
        chat = await ctx.bot.get_chat(ctx.channel_checker.chat_id)
        await message.answer(
            f"📋 <b>{chat.title}</b>\n🔗 <code>{link}</code>\n🆔 <code>{chat.id}</code>\n"
            f"🔐 {chat.type}",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ <code>{e}</code>", parse_mode="HTML")


@router.message(Command("clearchannel"), private_chat)
async def cmd_clear_channel(message: Message) -> None:
    if not await _admin_guard(message):
        return
    ctx = get_ctx()
    checker = ctx.channel_checker
    await ctx.channel.set_link("")
    checker.set_link("")
    await message.answer("✅ Проверка подписки отключена.")


# ===== Вспомогательное: подписка в подарок =====

def _expires_label(lang: str, expires_at: str | None) -> str:
    """Человекочитаемый срок действия подписки."""
    if not expires_at:
        return "—"
    return t(lang, "sub.lifetime") if is_lifetime(expires_at) else expires_at[:10]


def _id_from_cb(data: str) -> int | None:
    """Достаёт user_id из callback_data вида 'u:xx:<id>'."""
    try:
        return int(data.split(":")[2])
    except (IndexError, ValueError):
        return None


async def _gift_subscription(user_id: int, days: int | None, lifetime: bool) -> str | None:
    """Активирует Pro в подарок. None — если пользователь боту не писал."""
    ctx = get_ctx()
    if not await ctx.users.get(user_id):
        return None
    return await ctx.subs.activate(
        user_id=user_id, plan="pro",
        days=days if days is not None else GIFT_DEFAULT_DAYS,
        source="gift", lifetime=lifetime,
    )


async def _notify_gift(user_id: int, shown: str) -> None:
    """Сообщает получателю о подарке; неудача не критична."""
    ctx = get_ctx()
    target = await ctx.users.get(user_id)
    tlang = (target or {}).get("language") or "ru"
    try:
        await ctx.bot.send_message(
            user_id, t(tlang, "gift.notify", expires=shown), parse_mode="HTML"
        )
    except Exception as e:
        logger.info("Не удалось уведомить %s о подарке: %s", user_id, e)


def _parse_gift_args(args: list[str]) -> tuple[int, int | None, bool] | None:
    """'/gift <ID> [дней|life]' → (user_id, days, lifetime) либо None."""
    if not args or not args[0].lstrip("-").isdigit():
        return None
    user_id = int(args[0])
    if len(args) == 1:
        return user_id, None, False
    second = args[1].lower()
    if second in LIFETIME_WORDS:
        return user_id, None, True
    if not second.isdigit():
        return None
    days = int(second)
    return user_id, (days if days >= 1 else GIFT_DEFAULT_DAYS), False


@router.message(Command("gift"), private_chat)
async def cmd_gift(message: Message, command: CommandObject) -> None:
    if not await _admin_guard(message):
        return
    admin = await ensure_user(message)
    lang = admin["language"]

    parsed = _parse_gift_args((command.args or "").split())
    if parsed is None:
        key = "gift.usage" if not (command.args or "").strip() else "gift.bad_args"
        await message.answer(t(lang, key), parse_mode="HTML")
        return

    user_id, days, lifetime = parsed
    expires = await _gift_subscription(user_id, days, lifetime)
    if expires is None:
        await message.answer(
            t(lang, "gift.user_not_found", user_id=user_id), parse_mode="HTML"
        )
        return

    shown = _expires_label(lang, expires)
    await message.answer(
        t(lang, "gift.success", user_id=user_id, expires=shown), parse_mode="HTML"
    )
    logger.info("Админ %s подарил Pro → %s (lifetime=%s)", admin["user_id"], user_id, lifetime)
    await _notify_gift(user_id, shown)


# ===== Карточка пользователя =====

async def _render_card(lang: str, user_id: int):
    """Текст и клавиатура карточки пользователя."""
    ctx = get_ctx()
    target = await ctx.users.get(user_id)
    if not target:
        return None, None

    sub = await ctx.subs.get_active(user_id)
    stats = await ctx.usage.stats_for_user(user_id)
    is_pro = bool(sub)
    text = t(lang, "card.title") + t(
        lang, "card.body",
        user_id=user_id,
        name=escape(target.get("first_name") or "—"),
        username=("@" + target["username"]) if target.get("username") else "—",
        lang=lang_name(target.get("language") or "ru"),
        plan="⭐ Pro" if is_pro else "Free",
        expires=_expires_label(lang, sub["expires_at"] if sub else None),
        quality=target.get("quality") or "—",
        total=stats["ok"], errors=stats["errors"], today=stats["today"],
        last_seen=(target.get("last_seen") or "—")[:16],
        created=(target.get("created_at") or "—")[:10],
    )
    return text, menus.user_card_menu(lang, user_id, is_pro)


async def _refresh_card(cb: CallbackQuery, lang: str, user_id: int) -> None:
    text, markup = await _render_card(lang, user_id)
    if text:
        await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("u:v:"))
async def cb_user_card(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    admin = await ensure_user(cb)
    lang = admin["language"]
    user_id = _id_from_cb(cb.data)
    if user_id is None:
        await cb.answer()
        return
    text, markup = await _render_card(lang, user_id)
    if text is None:
        await cb.answer(
            t(lang, "gift.user_not_found", user_id=user_id), show_alert=True
        )
        return
    await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("u:gf:"))
async def cb_user_gift(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    admin = await ensure_user(cb)
    lang = admin["language"]
    user_id = _id_from_cb(cb.data)
    if user_id is None:
        await cb.answer()
        return

    expires = await _gift_subscription(user_id, None, False)
    if expires is None:
        await cb.answer(
            t(lang, "gift.user_not_found", user_id=user_id), show_alert=True
        )
        return

    shown = _expires_label(lang, expires)
    await cb.answer(t(lang, "gift.success", user_id=user_id, expires=shown), show_alert=True)
    logger.info("Админ %s подарил Pro → %s", admin["user_id"], user_id)
    await _notify_gift(user_id, shown)
    await _refresh_card(cb, lang, user_id)


@router.callback_query(F.data.startswith("u:rv:"))
async def cb_user_revoke(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    admin = await ensure_user(cb)
    lang = admin["language"]
    user_id = _id_from_cb(cb.data)
    if user_id is None:
        await cb.answer()
        return

    cancelled = await _cancel_subscription(user_id)
    if cancelled is None:
        await cb.answer(t(lang, "revoke.none", user_id=user_id), show_alert=True)
        return

    await cb.answer(
        t(lang, "revoke.done", user_id=user_id,
          expires=_expires_label(lang, cancelled["expires_at"])),
        show_alert=True,
    )
    logger.info("Админ %s снял подписку с %s", admin["user_id"], user_id)
    await _notify_cancelled(user_id)
    await _refresh_card(cb, lang, user_id)


# ===== Команда отмены подписки =====

async def _cancel_subscription(user_id: int) -> dict | None:
    """Отменяет активную подписку пользователя (админское действие)."""
    return await get_ctx().subs.cancel(user_id, reason="admin")


async def _notify_cancelled(user_id: int) -> None:
    """Сообщает пользователю об отмене подписки; неудача не критична."""
    ctx = get_ctx()
    target = await ctx.users.get(user_id)
    tlang = (target or {}).get("language") or "ru"
    try:
        await ctx.bot.send_message(user_id, t(tlang, "sub.cancel_admin"), parse_mode="HTML")
    except Exception as e:
        logger.info("Не удалось уведомить %s об отмене подписки: %s", user_id, e)


@router.message(Command("revoke"), private_chat)
async def cmd_revoke(message: Message, command: CommandObject) -> None:
    if not await _admin_guard(message):
        return
    admin = await ensure_user(message)
    lang = admin["language"]

    args = (command.args or "").split()
    if not args or not args[0].lstrip("-").isdigit():
        key = "revoke.usage" if not (command.args or "").strip() else "revoke.bad_args"
        await message.answer(t(lang, key), parse_mode="HTML")
        return

    user_id = int(args[0])
    cancelled = await _cancel_subscription(user_id)
    if cancelled is None:
        await message.answer(t(lang, "revoke.none", user_id=user_id), parse_mode="HTML")
        return

    await message.answer(
        t(lang, "revoke.done", user_id=user_id,
          expires=_expires_label(lang, cancelled["expires_at"])),
        parse_mode="HTML",
    )
    logger.info("Админ %s отменил подписку %s (была до %s)",
                admin["user_id"], user_id, cancelled["expires_at"])
    await _notify_cancelled(user_id)


# ===== Экспорт пользователей в CSV =====

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
        tempfile.gettempdir(), f"users_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
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
        try:
            os.unlink(path)
        except OSError:
            pass
        return

    try:
        await status.delete()
        await message.answer_document(
            FSInputFile(path, filename="users.csv"),
            caption=t(
                lang, "export.caption",
                count=count, date=f"{datetime.utcnow():%Y-%m-%d %H:%M}",
            ),
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@router.callback_query(F.data == "a:export")
async def cb_export(cb: CallbackQuery) -> None:
    if not await _admin_guard(cb):
        return
    admin = await ensure_user(cb)
    await cb.answer()
    await _send_export(cb.message, admin["language"])


@router.message(Command("exportusers"), private_chat)
async def cmd_export_users(message: Message) -> None:
    if not await _admin_guard(message):
        return
    admin = await ensure_user(message)
    await _send_export(message, admin["language"])
