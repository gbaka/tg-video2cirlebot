"""
Карточка пользователя, подарок и отмена подписки
"""

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import menus
from access import ensure_user
from context import get_ctx
from handlers.admin.shared import (
    GIFT_DEFAULT_DAYS,
    LIFETIME_WORDS,
    admin_guard,
    private_chat,
)
from i18n import lang_name, t
from repositories import is_lifetime
from tg import cb_data, cb_message

logger = logging.getLogger(__name__)
router = Router(name="admin.cards")

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
    if not await admin_guard(message):
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

async def _render_card(
    lang: str, user_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Текст и клавиатура карточки пользователя, либо None — если такого нет."""
    ctx = get_ctx()
    target = await ctx.users.get(user_id)
    if not target:
        return None

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
    card = await _render_card(lang, user_id)
    if card is not None:
        text, markup = card
        await cb_message(cb).edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("u:v:"))
async def cb_user_card(cb: CallbackQuery) -> None:
    if not await admin_guard(cb):
        return
    admin = await ensure_user(cb)
    lang = admin["language"]
    user_id = _id_from_cb(cb_data(cb))
    if user_id is None:
        await cb.answer()
        return
    card = await _render_card(lang, user_id)
    if card is None:
        await cb.answer(t(lang, "gift.user_not_found", user_id=user_id), show_alert=True)
        return
    text, markup = card
    await cb_message(cb).edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("u:gf:"))
async def cb_user_gift(cb: CallbackQuery) -> None:
    if not await admin_guard(cb):
        return
    admin = await ensure_user(cb)
    lang = admin["language"]
    user_id = _id_from_cb(cb_data(cb))
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
    if not await admin_guard(cb):
        return
    admin = await ensure_user(cb)
    lang = admin["language"]
    user_id = _id_from_cb(cb_data(cb))
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
    if not await admin_guard(message):
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
