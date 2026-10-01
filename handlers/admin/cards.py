"""
Карточка пользователя, подарок и отмена подписки.

Операции с подписками выполняет `services.subscriptions`; здесь — разбор
callback-ов и отрисовка.
"""

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import menus
from access import ensure_user
from context import get_ctx
from handlers.admin.shared import admin_guard, private_chat
from i18n import lang_name, t
from services.subscriptions import (
    cancel_subscription,
    expires_label,
    gift_subscription,
    notify_cancelled,
    notify_gift,
    parse_gift_args,
)
from tg import cb_data, cb_message

logger = logging.getLogger(__name__)
router = Router(name="admin.cards")


def _id_from_cb(data: str) -> int | None:
    """Достаёт user_id из callback_data вида 'u:xx:<id>'."""
    try:
        return int(data.split(":")[2])
    except (IndexError, ValueError):
        return None


async def _render_card(lang: str, user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Текст и клавиатура карточки пользователя, либо None — если такого нет."""
    ctx = get_ctx()
    target = await ctx.users.get(user_id)
    if not target:
        return None

    sub = await ctx.subs.get_active(user_id)
    stats = await ctx.usage.stats_for_user(user_id)
    is_pro = bool(sub)
    text = t(lang, "card.title") + t(
        lang,
        "card.body",
        user_id=user_id,
        name=escape(target.get("first_name") or "—"),
        username=("@" + target["username"]) if target.get("username") else "—",
        lang=lang_name(target.get("language") or "ru"),
        plan="⭐ Pro" if is_pro else "Free",
        expires=expires_label(lang, sub["expires_at"] if sub else None),
        quality=target.get("quality") or "—",
        total=stats["ok"],
        errors=stats["errors"],
        today=stats["today"],
        last_seen=(target.get("last_seen") or "—")[:16],
        created=(target.get("created_at") or "—")[:10],
    )
    return text, menus.user_card_menu(lang, user_id, is_pro)


async def _refresh_card(cb: CallbackQuery, lang: str, user_id: int) -> None:
    card = await _render_card(lang, user_id)
    if card is not None:
        text, markup = card
        await cb_message(cb).edit_text(text, reply_markup=markup, parse_mode="HTML")


@router.message(Command("gift"), private_chat)
async def cmd_gift(message: Message, command: CommandObject) -> None:
    if not await admin_guard(message):
        return
    admin = await ensure_user(message)
    lang = admin["language"]

    parsed = parse_gift_args((command.args or "").split())
    if parsed is None:
        key = "gift.usage" if not (command.args or "").strip() else "gift.bad_args"
        await message.answer(t(lang, key), parse_mode="HTML")
        return

    user_id, days, lifetime = parsed
    expires = await gift_subscription(get_ctx(), user_id, days, lifetime)
    if expires is None:
        await message.answer(t(lang, "gift.user_not_found", user_id=user_id), parse_mode="HTML")
        return

    shown = expires_label(lang, expires)
    await message.answer(
        t(lang, "gift.success", user_id=user_id, expires=shown), parse_mode="HTML"
    )
    logger.info("Админ %s подарил Pro → %s (lifetime=%s)", admin["user_id"], user_id, lifetime)
    await notify_gift(get_ctx(), user_id, shown)


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

    expires = await gift_subscription(get_ctx(), user_id, None, False)
    if expires is None:
        await cb.answer(t(lang, "gift.user_not_found", user_id=user_id), show_alert=True)
        return

    shown = expires_label(lang, expires)
    await cb.answer(t(lang, "gift.success", user_id=user_id, expires=shown), show_alert=True)
    logger.info("Админ %s подарил Pro → %s", admin["user_id"], user_id)
    await notify_gift(get_ctx(), user_id, shown)
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

    cancelled = await cancel_subscription(get_ctx(), user_id)
    if cancelled is None:
        await cb.answer(t(lang, "revoke.none", user_id=user_id), show_alert=True)
        return

    await cb.answer(
        t(
            lang,
            "revoke.done",
            user_id=user_id,
            expires=expires_label(lang, cancelled["expires_at"]),
        ),
        show_alert=True,
    )
    logger.info("Админ %s снял подписку с %s", admin["user_id"], user_id)
    await notify_cancelled(get_ctx(), user_id)
    await _refresh_card(cb, lang, user_id)


@router.message(Command("revoke"), private_chat)
async def cmd_revoke(message: Message, command: CommandObject) -> None:
    if not await admin_guard(message):
        return
    admin = await ensure_user(message)
    lang = admin["language"]

    args = (command.args or "").split()
    if (len(args) != 1 or not args[0].isascii() or not args[0].isdigit()
            or len(args[0]) > 16 or not 0 < int(args[0]) < 2**52):
        key = "revoke.usage" if not (command.args or "").strip() else "revoke.bad_args"
        await message.answer(t(lang, key), parse_mode="HTML")
        return

    user_id = int(args[0])
    cancelled = await cancel_subscription(get_ctx(), user_id)
    if cancelled is None:
        await message.answer(t(lang, "revoke.none", user_id=user_id), parse_mode="HTML")
        return

    await message.answer(
        t(
            lang,
            "revoke.done",
            user_id=user_id,
            expires=expires_label(lang, cancelled["expires_at"]),
        ),
        parse_mode="HTML",
    )
    logger.info(
        "Админ %s отменил подписку %s (была до %s)",
        admin["user_id"],
        user_id,
        cancelled["expires_at"],
    )
    await notify_cancelled(get_ctx(), user_id)
