"""
Проверки доступа: пользователь, роль, тариф, подписка, членство в канале.
"""

import json
import logging

from aiogram.types import CallbackQuery, Message

from context import get_ctx
from plans import Plan

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in get_ctx().config.bot.admin_ids


async def ensure_user(event: Message | CallbackQuery) -> dict:
    """Возвращает (создаёт при необходимости) запись пользователя."""
    ctx = get_ctx()
    tg = event.from_user
    if tg is None:  # например, сообщение от имени канала
        raise ValueError("Событие без пользователя")
    return await ctx.users.get_or_create(
        user_id=tg.id,
        username=tg.username,
        first_name=tg.full_name or tg.first_name,
        default_language=ctx.config.default_language,
    )


async def user_plan(user: dict) -> tuple[Plan, dict | None]:
    """
    Определяет действующий тариф пользователя.
    Возвращает (Plan, активная_подписка|None).
    """
    ctx = get_ctx()
    admin = is_admin(user["user_id"])
    sub = None if admin else await ctx.subs.get_active(user["user_id"])
    plan = ctx.plans.resolve(
        is_admin=admin,
        has_active_subscription=bool(sub),
        subscription_plan=sub["plan"] if sub else None,
    )
    return plan, sub


async def membership_required() -> bool:
    """Включена ли проверка подписки и задан ли канал."""
    ctx = get_ctx()
    if not await ctx.settings.get_bool("membership_check", True):
        return False
    link = await ctx.channel.get_link()
    return bool(link)


async def membership_ok(user_id: int) -> bool:
    """Проверяет, является ли пользователь участником канала/чата."""
    ctx = get_ctx()
    link = await ctx.channel.get_link()
    if not link:
        return True
    checker = ctx.channel_checker
    if checker.channel_link != link:
        checker.set_link(link)
    return await checker.is_member(user_id)


async def in_maintenance() -> bool:
    return await get_ctx().settings.get_bool("maintenance", False)


async def get_invite_url() -> str:
    ctx = get_ctx()
    link = await ctx.channel.get_link()
    checker = ctx.channel_checker
    if checker.channel_link != link:
        checker.set_link(link)
    try:
        saved = json.loads(await ctx.settings.get("channel_invite_link", "{}") or "{}")
        url = saved.get("url", "") if saved.get("channel") == link else ""
        checker.set_invite_link(url)
    except (ValueError, TypeError, AttributeError):
        checker.set_invite_link(None)
    public_url = checker.get_join_url()
    return public_url or await checker.get_chat_invite_link() or ""


async def user_lang(user: dict) -> str:
    return user.get("language") or get_ctx().config.default_language
