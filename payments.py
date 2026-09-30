"""
Оплата подписок через Telegram Stars (валюта XTR).

Флоу:
  1. Пользователь выбирает тариф → send_invoice (currency=XTR, provider_token="").
  2. Telegram шлёт pre_checkout_query → отвечаем ok.
  3. Telegram шлёт message.successful_payment → активируем подписку.
"""

import logging

from aiogram import Bot
from aiogram.types import LabeledPrice

from plans import PriceOption

logger = logging.getLogger(__name__)

STARS_CURRENCY = "XTR"


def make_payload(price: PriceOption) -> str:
    return f"sub|{price.code}|{price.plan}|{price.days}|{price.stars}|{1 if price.lifetime else 0}"


def parse_payload(payload: str) -> dict | None:
    """Разбирает payload платежа. Возвращает dict или None при несовпадении."""
    if not payload or not payload.startswith("sub|"):
        return None
    parts = payload.split("|")
    if len(parts) not in (5, 6):        # 5 — счета, отправленные до появления lifetime
        return None
    lifetime = bool(int(parts[5])) if len(parts) == 6 and parts[5].isdigit() else False
    _, code, plan, days, stars = parts[:5]
    try:
        return {
            "code": code, "plan": plan, "days": int(days),
            "stars": int(stars), "lifetime": lifetime,
        }
    except ValueError:
        return None


async def send_subscription_invoice(
    bot: Bot, chat_id: int, price: PriceOption, title: str, description: str
) -> None:
    """Отправляет счёт на оплату подписки в Telegram Stars."""
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=make_payload(price),
        provider_token="",                 # для Stars не нужен
        currency=STARS_CURRENCY,
        prices=[LabeledPrice(label=title, amount=price.stars)],
        start_parameter=f"sub-{price.code}",
    )
