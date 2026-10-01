"""
Оплата подписок через Telegram Stars (валюта XTR).

Флоу:
  1. Пользователь выбирает тариф → send_invoice (currency=XTR, provider_token="").
  2. Telegram шлёт pre_checkout_query → отвечаем ok.
  3. Telegram шлёт message.successful_payment → активируем подписку.
"""

import logging
import re

from aiogram import Bot
from aiogram.types import LabeledPrice

from plans import PriceOption
from repositories import PaymentRepo

logger = logging.getLogger(__name__)

STARS_CURRENCY = "XTR"


def make_payload(price: PriceOption) -> str:
    return f"sub|{price.code}|{price.plan}|{price.days}|{price.stars}|{1 if price.lifetime else 0}"


def parse_payload(payload: str) -> dict | None:
    """Strict legacy parser; price snapshots are intentionally not compared to current prices."""
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > 128:
        return None
    parts = payload.split("|")
    if len(parts) not in (5, 6) or parts[0] != "sub":
        return None
    _, code, plan, days, stars = parts[:5]
    if not code or plan != "pro":
        return None
    if len(parts) == 6 and parts[5] not in ("0", "1"):
        return None
    lifetime = len(parts) == 6 and parts[5] == "1"
    if not re.fullmatch(r"[0-9]{1,7}", days) or not re.fullmatch(r"[0-9]{1,10}", stars):
        return None
    duration, amount = int(days), int(stars)
    if amount <= 0 or duration > 365000 or (not lifetime and duration <= 0):
        return None
    return {"code": code, "plan": plan, "days": duration, "stars": amount, "lifetime": lifetime}


async def validate_payment(
    payments: PaymentRepo,
    payload: str,
    user_id: int,
    currency: str,
    total_amount: int,
) -> dict | None:
    """Resolve issued snapshots or historical payloads and verify Telegram's actual charge."""
    if currency != STARS_CURRENCY or type(total_amount) is not int or total_amount <= 0:
        return None
    if payload.startswith("sub2|"):
        data = await payments.get_invoice(payload, user_id)
        if data:
            data["lifetime"] = bool(data["lifetime"])
    else:
        data = parse_payload(payload)
    if not data or data["stars"] != total_amount:
        return None
    return data


async def send_subscription_invoice(
    bot: Bot,
    chat_id: int,
    price: PriceOption,
    title: str,
    description: str,
    *,
    payments: PaymentRepo | None = None,
) -> None:
    """Отправляет счёт на оплату подписки в Telegram Stars."""
    payload = make_payload(price)
    if parse_payload(payload) is None:
        raise ValueError("Invalid invoice price")
    if payments is not None:
        payload = await payments.issue_invoice(
            chat_id, price.code, price.plan, price.days, price.stars, price.lifetime
        )
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",  # для Stars не нужен
        currency=STARS_CURRENCY,
        prices=[LabeledPrice(label=title, amount=price.stars)],
        start_parameter=f"sub-{price.code}",
    )
