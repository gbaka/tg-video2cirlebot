"""
Статистика и панель текущих задач
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import menus
from access import ensure_user
from context import get_ctx
from handlers.admin.shared import (
    admin_guard,
    private_chat,
)
from i18n import t
from tg import cb_message
from timeutil import ago

logger = logging.getLogger(__name__)
router = Router(name="admin.stats")

async def _render_stats(lang: str) -> str:
    ctx = get_ctx()
    users_total = await ctx.users.count_all()
    users_new_day = await ctx.users.count_since(ago(days=1))
    users_new_week = await ctx.users.count_since(ago(days=7))
    users_active_day = await ctx.users.count_active_since(ago(days=1))

    conv_total = await ctx.usage.count_all()
    conv_day = await ctx.usage.count_since(ago(days=1))
    conv_week = await ctx.usage.count_since(ago(days=7))
    errors_week = await ctx.usage.count_since(ago(days=7), status="error")
    avg_ms = await ctx.usage.avg_processing_ms(ago(days=7))
    total_bytes = await ctx.usage.total_bytes(ago(days=7))

    subs_active = await ctx.subs.count_active()
    pay_count = await ctx.payments.count()
    stars_total = await ctx.payments.total_stars()

    top = await ctx.usage.top_formats(ago(days=30), limit=5)
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
    if not await admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    text = await _render_stats(lang)
    await cb_message(cb).edit_text(text, reply_markup=menus.admin_menu(lang), parse_mode="HTML")
    await cb.answer()


@router.message(Command("stats"), private_chat)
async def cmd_stats(message: Message) -> None:
    if not await admin_guard(message):
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
    if not await admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        await _render_tasks(lang), reply_markup=menus.admin_menu(lang), parse_mode="HTML"
    )
    await cb.answer()


@router.message(Command("tasks"), private_chat)
async def cmd_tasks(message: Message) -> None:
    if not await admin_guard(message):
        return
    user = await ensure_user(message)
    await message.answer(await _render_tasks(user["language"]), parse_mode="HTML")
