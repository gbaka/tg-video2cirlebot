"""
Админские хендлеры: статистика, задачи, пользователи, настройки, канал.
"""

import logging
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import menus
from access import ensure_user, is_admin, user_lang
from context import get_ctx
from i18n import t
from repositories import _ago

logger = logging.getLogger(__name__)
router = Router()

private_chat = F.chat.type == "private"

USERS_PAGE_SIZE = 10


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

    markup = menus.users_page_menu(lang, offset, USERS_PAGE_SIZE, total, query)
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
    checker.channel_link = args
    checker._channel_id = None
    checker._channel_username = None
    checker._parse_channel_link()
    try:
        chat = await ctx.bot.get_chat(checker.chat_id)
        await ctx.channel.set_link(args)
        await message.answer(
            f"✅ <b>{chat.title}</b>\n🔗 <code>{args}</code>\n🔐 {chat.type}\n"
            f"👥 {getattr(chat, 'participants_count', '?')}",
            parse_mode="HTML",
        )
    except Exception as e:
        checker.channel_link = old
        checker._channel_id = None
        checker._channel_username = None
        checker._parse_channel_link()
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
    checker.channel_link = ""
    checker._channel_id = None
    checker._channel_username = None
    checker._parse_channel_link()
    await message.answer("✅ Проверка подписки отключена.")
