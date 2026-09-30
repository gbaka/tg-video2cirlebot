"""
Оплата подписок через Telegram Stars (валюта XTR).

Флоу:
  1. Пользователь выбирает тариф → send_invoice (currency=XTR, provider_token="").
  2. Telegram шлёт pre_checkout_query → отвечаем ok.
  3. Telegram шлёт message.successful_payment → активируем подписку.
"""

import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import LabeledPrice

from plans import PriceOption

logger = logging.getLogger(__name__)

STARS_CURRENCY = "XTR"


def make_payload(price: PriceOption) -> str:
    return f"sub|{price.code}|{price.plan}|{price.days}|{price.stars}"


def parse_payload(payload: str) -> Optional[dict]:
    """Разбирает payload платежа. Возвращает dict или None при несовпадении."""
    if not payload or not payload.startswith("sub|"):
        return None
    parts = payload.split("|")
    if len(parts) != 5:
        return None
    _, code, plan, days, stars = parts
    try:
        return {"code": code, "plan": plan, "days": int(days), "stars": int(stars)}
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
