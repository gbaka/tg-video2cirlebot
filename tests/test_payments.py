"""Платежи: payload счетов и совместимость форматов."""

import pytest

from payments import make_payload, parse_payload
from plans import PriceOption


def test_payload_round_trip() -> None:
    price = PriceOption("pro_90d", "pro", 90, 60)
    assert parse_payload(make_payload(price)) == {
        "code": "pro_90d", "plan": "pro", "days": 90, "stars": 60, "lifetime": False,
    }


def test_lifetime_payload() -> None:
    price = PriceOption("pro_life", "pro", 0, 150, lifetime=True)
    assert parse_payload(make_payload(price)) == {
        "code": "pro_life", "plan": "pro", "days": 0, "stars": 150, "lifetime": True,
    }


def test_legacy_payload_without_lifetime_flag() -> None:
    """Счета, выставленные до появления бессрочной подписки, должны читаться."""
    assert parse_payload("sub|pro_30d|pro|30|100") == {
        "code": "pro_30d", "plan": "pro", "days": 30, "stars": 100, "lifetime": False,
    }


@pytest.mark.parametrize("payload", ["", "garbage", "sub|a|b", "sub|a|b|c", "sub|a|b|c|d|e|f"])
def test_invalid_payloads(payload: str) -> None:
    assert parse_payload(payload) is None
