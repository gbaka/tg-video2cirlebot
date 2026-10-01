"""
Подписки: выдача в подарок, отмена, уведомления получателю.

Все операции идут через репозитории, поэтому пригодны не только для
команд бота, но и для скриптов обслуживания.
"""

import logging
import re
from typing import Any

from context import AppContext
from i18n import t
from repositories import is_lifetime

logger = logging.getLogger(__name__)

#: Срок подарка по умолчанию (если не указан явно)
GIFT_DEFAULT_DAYS = 30

#: Слова, которыми в команде /gift обозначают бессрочную подписку
LIFETIME_WORDS = {"life", "lifetime", "forever", "навсегда", "вечно"}


def expires_label(lang: str, expires_at: str | None) -> str:
    """Человекочитаемый срок действия подписки."""
    if not expires_at:
        return "—"
    return t(lang, "sub.lifetime") if is_lifetime(expires_at) else expires_at[:10]


def parse_gift_args(args: list[str]) -> tuple[int, int | None, bool] | None:
    """'/gift <ID> [дней|life]' → (user_id, days, lifetime) либо None."""
    if len(args) not in (1, 2) or not re.fullmatch(r"-?[0-9]{1,18}", args[0]):
        return None
    user_id = int(args[0])
    if len(args) == 1:
        return user_id, None, False
    second = args[1].lower()
    if second in LIFETIME_WORDS:
        return user_id, None, True
    if not re.fullmatch(r"[0-9]{1,6}", second):
        return None
    days = int(second)
    return user_id, (days if days >= 1 else GIFT_DEFAULT_DAYS), False


async def gift_subscription(
    ctx: AppContext, user_id: int, days: int | None, lifetime: bool
) -> str | None:
    """Активирует Pro в подарок. None — если пользователь боту не писал."""
    if not await ctx.users.get(user_id):
        return None
    return await ctx.subs.activate(
        user_id=user_id,
        plan="pro",
        days=days if days is not None else GIFT_DEFAULT_DAYS,
        source="gift",
        lifetime=lifetime,
    )


async def cancel_subscription(ctx: AppContext, user_id: int) -> dict[str, Any] | None:
    """Отменяет активную подписку пользователя (действие администратора)."""
    return await ctx.subs.cancel(user_id, reason="admin")


async def notify_gift(ctx: AppContext, user_id: int, shown: str) -> None:
    """Сообщает получателю о подарке; неудача не критична."""
    await _notify(ctx, user_id, "gift.notify", expires=shown)


async def notify_cancelled(ctx: AppContext, user_id: int) -> None:
    """Сообщает пользователю об отмене подписки; неудача не критична."""
    await _notify(ctx, user_id, "sub.cancel_admin")


async def _notify(ctx: AppContext, user_id: int, key: str, **kwargs: Any) -> None:
    """Отправляет пользователю уведомление на его языке, глотая ошибки."""
    target = await ctx.users.get(user_id)
    lang = (target or {}).get("language") or "ru"
    try:
        await ctx.bot.send_message(user_id, t(lang, key, **kwargs), parse_mode="HTML")
    except Exception as e:
        logger.info("Не удалось уведомить %s (%s): %s", user_id, key, e)
