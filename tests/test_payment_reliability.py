"""Atomic Stars fulfillment against real SQLite, including crash retries."""

import asyncio

import aiosqlite
import pytest


async def test_payment_grant_rollback_then_retry(payments, subs, db):
    async with db.connect() as conn:
        await conn.execute(
            "CREATE TRIGGER fail_grant BEFORE INSERT ON subscriptions "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        await conn.commit()
    with pytest.raises(aiosqlite.IntegrityError, match="injected"):
        await payments.record_and_activate(42, "charge", "pro", 30, 100)
    assert await payments.count() == 0
    assert await subs.get_active(42) is None
    async with db.connect() as conn:
        await conn.execute("DROP TRIGGER fail_grant")
        await conn.commit()
    fresh, expires = await payments.record_and_activate(42, "charge", "pro", 30, 100)
    assert fresh and expires == (await subs.get_active(42))["expires_at"]
    results = await asyncio.gather(
        *(payments.record_and_activate(42, "charge", "pro", 30, 100) for _ in range(4))
    )
    assert results == [(False, expires)] * 4
    assert await payments.count() == 1


async def test_legacy_stranded_payment_is_recovered(payments, subs):
    await payments.add(42, "stranded", "pro", 30, 100)
    fresh, expires = await payments.record_and_activate(42, "stranded", "pro", 30, 100)
    assert fresh and expires == (await subs.get_active(42))["expires_at"]
    assert await payments.record_and_activate(42, "stranded", "pro", 30, 100) == (False, expires)


async def test_legacy_noop_on_lifetime_is_not_regranted_after_cancel(payments, subs, db):
    """A charge taken while a lifetime grant was already active owes nothing.

    The old flow recorded the payment and then no-op'd, leaving no subscription
    carrying that charge.  Re-driving it after the user cancels must not hand out
    a fresh term.
    """
    await subs.activate(42, "pro", 0, "admin", lifetime=True)
    async with db.connect() as conn:
        # Pin the timeline: the lifetime grant provably preceded the payment.
        await conn.execute(
            "UPDATE subscriptions SET started_at = '2026-01-01 00:00:00' WHERE user_id = 42"
        )
        await conn.commit()
    await payments.add(42, "legacy-noop", "pro", 30, 50)
    await subs.activate(42, "pro", 30, "stars", charge_id="legacy-noop")
    assert (await subs.get_active(42))["expires_at"].startswith("9999")

    assert await subs.cancel(42, reason="user") is not None
    assert await subs.get_active(42) is None

    fresh, _expires = await payments.record_and_activate(42, "legacy-noop", "pro", 30, 50)
    assert not fresh, "ambiguous legacy no-op charge was regranted"
    assert await subs.get_active(42) is None, "cancelled user got the term back"
    # and the ambiguity is resolved permanently, not re-evaluated on every redelivery
    assert await payments.record_and_activate(42, "legacy-noop", "pro", 30, 50) == (
        False,
        _expires,
    )


async def test_legacy_stranded_payment_after_lifetime_existed_is_still_recovered(payments, subs):
    """A lifetime grant created AFTER the payment does not excuse the missing grant."""
    await payments.add(42, "stranded-later", "pro", 30, 100)
    await subs.activate(42, "pro", 0, "admin", lifetime=True)
    await subs.cancel(42, reason="user")
    fresh, expires = await payments.record_and_activate(42, "stranded-later", "pro", 30, 100)
    assert fresh and expires == (await subs.get_active(42))["expires_at"]


@pytest.mark.parametrize(
    "payload",
    [
        "sub|p|pro|30|100|²",
        "sub|p|pro|30|100|2",
        "sub|p|pro|30|100|oops",
        "sub|p|pro|0|100",
        "sub|p|pro|-3|100",
        "sub|p|pro|30|0",
        "sub|p|pro|30|100|",
        "sub|p|free|30|100",
        "sub||pro|30|100",
        "sub|p|pro|３|100",
        "sub|p|pro|30|100|٠",
        "sub|p|pro|9999999999|100",
    ],
)
def test_payload_rejects_malformed_values(payload):
    from payments import parse_payload

    assert parse_payload(payload) is None


async def test_invoice_snapshot_validation_and_old_prices(payments):
    import payments as payment_flow
    from plans import PriceOption

    class Bot:
        async def send_invoice(self, **kwargs):
            self.invoice = kwargs

    bot = Bot()
    await payment_flow.send_subscription_invoice(
        bot, 42, PriceOption("old", "pro", 30, 19), "Title", "Desc", payments=payments
    )
    payload = bot.invoice["payload"]
    assert payload.startswith("sub2|")
    data = await payment_flow.validate_payment(payments, payload, 42, "XTR", 19)
    assert data["stars"] == 19 and data["days"] == 30
    assert await payment_flow.validate_payment(payments, payload, 43, "XTR", 19) is None
    assert await payment_flow.validate_payment(payments, payload, 42, "USD", 19) is None
    assert await payment_flow.validate_payment(payments, payload, 42, "XTR", 20) is None
    assert await payment_flow.validate_payment(payments, "sub2|unknown", 42, "XTR", 19) is None
    for old in ["sub|old|pro|30|19", "sub|old|pro|30|19|0"]:
        assert (await payment_flow.validate_payment(payments, old, 42, "XTR", 19))["stars"] == 19
        assert await payment_flow.validate_payment(payments, old, 42, "XTR", 20) is None


async def test_billing_rejects_checkout_and_fulfills_atomically(payments, subs, users, monkeypatch):
    from types import SimpleNamespace

    from handlers.user import billing

    ctx = SimpleNamespace(
        payments=payments, subs=subs, config=SimpleNamespace(default_language="ru")
    )
    monkeypatch.setattr(billing, "get_ctx", lambda: ctx)
    user = await users.get_or_create(42, None, None)

    async def ensure(_):
        return user

    monkeypatch.setattr(billing, "ensure_user", ensure)

    class Event:
        from_user = SimpleNamespace(id=42)
        invoice_payload = "sub|old|pro|30|19"
        currency = "USD"
        total_amount = 19

        async def answer(self, *args, **kwargs):
            self.response = (args, kwargs)

    checkout = Event()
    await billing.on_pre_checkout(checkout)
    assert checkout.response[1]["ok"] is False
    checkout.currency = "XTR"
    await billing.on_pre_checkout(checkout)
    assert checkout.response[1]["ok"] is True
    message = Event()
    message.successful_payment = SimpleNamespace(
        invoice_payload=checkout.invoice_payload,
        currency="USD",
        total_amount=19,
        telegram_payment_charge_id="handler-charge",
    )
    await billing.on_successful_payment(message)
    assert await payments.count() == 0
    message.successful_payment.currency = "XTR"
    monkeypatch.setattr(billing, "is_admin", lambda _: False)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Non-atomic activation called")

    monkeypatch.setattr(subs, "activate", forbidden)
    await billing.on_successful_payment(message)
    assert await payments.count() == 1 and await subs.get_active(42)
    await billing.on_successful_payment(message)
    assert await payments.count() == 1


@pytest.mark.parametrize("args", [["²"], ["42", "²"], ["--42"], ["42", "1", "extra"]])
def test_malformed_gift_arguments_are_safe(args):
    from services.subscriptions import parse_gift_args

    assert parse_gift_args(args) is None


@pytest.mark.parametrize(
    "local,is_pro,expected",
    [(False, False, 20), (False, True, 20), (True, False, 50), (True, True, 200)],
)
async def test_subscription_menu_effective_download_size(monkeypatch, local, is_pro, expected):
    from types import SimpleNamespace

    from config_loader import APIConfig
    from handlers.user import billing
    from plans import Plan, Plans
    from tests.fakes import FakeStatus

    free = Plan("free", 50, 60, [360], 360, 23, "fast")
    pro = Plan("pro", 200, 60, [480], 480, 20, "fast")
    ctx = SimpleNamespace(
        config=SimpleNamespace(api=APIConfig(local=local)), plans=Plans(free, pro, [])
    )
    monkeypatch.setattr(billing, "get_ctx", lambda: ctx)

    async def ensure(_):
        return {"language": "ru"}

    async def user_plan(_):
        return (pro if is_pro else free), None

    monkeypatch.setattr(billing, "ensure_user", ensure)
    monkeypatch.setattr(billing, "user_plan", user_plan)
    status = FakeStatus()
    monkeypatch.setattr(billing, "cb_message", lambda _: status)

    class Callback:
        async def answer(self):
            pass

    original_menu = billing.menus.subscription_menu

    def checked_menu(lang, prices, is_pro=False):
        assert not is_pro, "Admin Pro access without a real subscription has nothing to cancel"
        return original_menu(lang, prices, is_pro)

    monkeypatch.setattr(billing.menus, "subscription_menu", checked_menu)
    await billing.cb_subscription(Callback())
    assert f"{expected}" in status.edits[0]
    if not local:
        assert "200" not in status.edits[0] and "50" not in status.edits[0]


async def test_redelivery_after_cancellation_does_not_reactivate(payments, subs):
    fresh, expires = await payments.record_and_activate(42, "once", "pro", 30, 100)
    assert fresh
    await subs.cancel(42)
    assert await payments.record_and_activate(42, "once", "pro", 30, 100) == (False, expires)
    assert await subs.get_active(42) is None


async def test_old_fulfilled_charge_not_regranted(payments, subs):
    await payments.add(42, "old", "pro", 30, 100)
    expires = await subs.activate(42, "pro", 30, "stars", "old")
    await subs.cancel(42)
    assert await payments.record_and_activate(42, "old", "pro", 30, 100) == (False, expires)
    assert await subs.get_active(42) is None


async def test_renewal_failure_rolls_back_deactivation(payments, subs, db):
    initial = await subs.activate(42, "pro", 10, "gift")
    async with db.connect() as conn:
        await conn.execute(
            "CREATE TRIGGER fail_renewal BEFORE INSERT ON subscriptions "
            "BEGIN SELECT RAISE(ABORT, 'renewal failed'); END"
        )
        await conn.commit()
    with pytest.raises(aiosqlite.IntegrityError, match="renewal failed"):
        await payments.record_and_activate(42, "renewal", "pro", 30, 100)
    assert await payments.count() == 0
    assert (await subs.get_active(42))["expires_at"] == initial
