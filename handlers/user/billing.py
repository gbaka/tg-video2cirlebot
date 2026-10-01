"""
Подписка Pro: покупка, отмена, оплата звёздами
"""

import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    Message,
    PreCheckoutQuery,
)

import menus
from access import (
    ensure_user,
    is_admin,
    membership_ok,
    user_plan,
)
from context import get_ctx
from handlers.user.shared import private_chat, show_main
from i18n import t
from payments import send_subscription_invoice, validate_payment
from repositories import is_lifetime
from tg import cb_data, cb_message

logger = logging.getLogger(__name__)
router = Router(name="user.billing")


@router.callback_query(F.data == "m:sub")
async def cb_subscription(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    plan, sub = await user_plan(user)
    free, pro = ctx.plans.free, ctx.plans.pro

    if plan.code == "pro":
        expires = (
            t(lang, "sub.lifetime")
            if is_lifetime(sub["expires_at"] if sub else None)
            else (sub["expires_at"][:10] if sub else "—")
        )
        text = t(lang, "sub.title") + t(
            lang,
            "sub.current_pro",
            maxres=max(pro.resolutions),
            size=ctx.config.api.input_limit_bytes(pro.max_size_bytes) // (1024 * 1024),
            expires=expires,
        )
    else:
        text = t(lang, "sub.title") + t(
            lang,
            "sub.current_free" if not free.is_unlimited() else "sub.current_free_unlimited",
            res=free.default_resolution,
            size=ctx.config.api.input_limit_bytes(free.max_size_bytes) // (1024 * 1024),
            limit=free.daily_limit,
            album=free.max_album,
        )

    if ctx.plans.prices:
        text += t(lang, "sub.buy_title")
    else:
        text += t(lang, "sub.no_prices")

    await cb_message(cb).edit_text(
        text,
        reply_markup=menus.subscription_menu(lang, ctx.plans.prices, bool(sub)),
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
    expires = t(lang, "sub.lifetime") if is_lifetime(sub["expires_at"]) else sub["expires_at"][:10]
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
        user["user_id"],
        cancelled["expires_at"],
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
    title = (
        t(lang, "sub.buy_btn_life", stars=price.stars)
        if price.lifetime
        else f"Pro · {price.days} дн."
        if lang == "ru"
        else f"Pro · {price.days}d"
    )
    desc = "Подписка Pro на видео-кружки" if lang == "ru" else "Pro subscription for video notes"
    await send_subscription_invoice(
        ctx.bot, cb.from_user.id, price, title, desc, payments=ctx.payments
    )
    await cb.answer()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    ctx = get_ctx()
    data = await validate_payment(
        ctx.payments, query.invoice_payload, query.from_user.id, query.currency, query.total_amount
    )
    if data:
        await query.answer(ok=True)
    else:
        await query.answer(
            ok=False, error_message=t(ctx.config.default_language, "sub.payment_error")
        )


@router.message(F.successful_payment, private_chat)
async def on_successful_payment(message: Message) -> None:
    ctx = get_ctx()
    user = await ensure_user(message)
    lang = user["language"]
    sp = message.successful_payment
    if sp is None:  # сообщение с типом successful_payment всегда его содержит
        logger.warning("Платёж без данных successful_payment")
        return

    data = await validate_payment(
        ctx.payments, sp.invoice_payload, user["user_id"], sp.currency, sp.total_amount
    )
    if not data:
        logger.warning("Неизвестный payload платежа: %s", sp.invoice_payload)
        await message.answer(t(lang, "sub.payment_error"), parse_mode="HTML")
        return

    # Идемпотентность по charge_id
    fresh, expires = await ctx.payments.record_and_activate(
        user_id=user["user_id"],
        charge_id=sp.telegram_payment_charge_id,
        plan=data["plan"],
        days=data["days"],
        stars=data["stars"],
        lifetime=data["lifetime"],
    )
    if not fresh:
        logger.info("Повторный платёж %s — игнорируем", sp.telegram_payment_charge_id)
        await message.answer(t(lang, "sub.already_paid"), parse_mode="HTML")
        return

    shown = t(lang, "sub.lifetime") if is_lifetime(expires) else expires[:10]
    await message.answer(
        t(lang, "sub.paid_success", expires=shown),
        reply_markup=menus.main_menu(lang, is_admin(user["user_id"])),
        parse_mode="HTML",
    )
    logger.info(
        "Оплата Stars: user=%s plan=%s days=%s stars=%s lifetime=%s",
        user["user_id"],
        data["plan"],
        data["days"],
        data["stars"],
        data.get("lifetime", False),
    )


# ===== Проверка подписки по кнопке =====


@router.callback_query(F.data == "c:check")
async def cb_check_membership(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    if is_admin(user["user_id"]) or await membership_ok(user["user_id"]):
        await cb.answer("✅")
        await show_main(cb, user)
    else:
        await cb.answer("❌", show_alert=True)
