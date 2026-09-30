"""Репозитории: пользователи, подписки, статистика, настройки, платежи."""

import aiosqlite
import pytest

from repositories import LIFETIME_EXPIRES, is_lifetime

# ===== Пользователи =====


async def test_get_or_create_is_idempotent(users) -> None:
    first = await users.get_or_create(111, "bob", "Bob", "ru")
    second = await users.get_or_create(111, "bob", "Bob", "ru")
    assert first["user_id"] == second["user_id"] == 111
    assert await users.count_all() == 1


async def test_language_and_quality_persist(users) -> None:
    await users.get_or_create(111, "bob", "Bob", "ru")
    await users.set_language(111, "en")
    await users.set_quality(111, 480)
    row = await users.get(111)
    assert row["language"] == "en"
    assert row["quality"] == 480


async def test_pagination(users) -> None:
    for i in range(25):
        await users.get_or_create(1000 + i, f"user{i}", f"Name{i}", "ru")
    page1, total = await users.search_page(0, 10)
    page3, _ = await users.search_page(20, 10)
    empty, _ = await users.search_page(999, 10)
    assert (len(page1), len(page3), empty) == (10, 5, [])
    assert total == 25


async def test_search_by_id_username_and_name(users, subs) -> None:
    for i in range(25):
        await users.get_or_create(1000 + i, f"user{i}", f"Name{i}", "ru")
    await subs.activate(1000, "pro", 30, "stars", "c1")

    by_id, total = await users.search_page(0, 10, "1005")
    assert total == 1 and by_id[0]["user_id"] == 1005

    by_username, total = await users.search_page(0, 10, "@user3")
    assert total == 1 and by_username[0]["user_id"] == 1003

    _by_name, total = await users.search_page(0, 10, "Name1")
    assert total >= 11          # Name1, Name10..Name19

    nothing, total = await users.search_page(0, 10, "zzzzz")
    assert nothing == [] and total == 0


async def test_active_plan_comes_from_the_same_query(users, subs) -> None:
    """Тариф подтягивается подзапросом — без отдельного запроса на строку."""
    await users.get_or_create(1000, "a", "A", "ru")
    await users.get_or_create(1001, "b", "B", "ru")
    await subs.activate(1000, "pro", 30, "stars", "c1")

    rows, _ = await users.search_page(0, 10)
    plans = {r["user_id"]: r["active_plan"] for r in rows}
    assert plans == {1000: "pro", 1001: None}


async def test_export_page_has_all_columns(users, subs, usage) -> None:
    await users.get_or_create(1, "a", "A", "ru")
    await subs.activate(1, "pro", 30, "stars", "c1")
    await usage.add(1, "ok", "pro", fmt=".mp4")
    await usage.add(1, "error", "pro", fmt=".mov", error="boom")

    rows = await users.export_page(0, 100)
    assert len(rows) == 1
    row = rows[0]
    assert row["active_plan"] == "pro" and row["conv_ok"] == 1 and row["conv_err"] == 1
    assert await users.export_page(100, 100) == []


async def test_counts_since(users) -> None:
    await users.get_or_create(1, "a", "A", "ru")
    assert await users.count_since("2000-01-01 00:00:00") == 1
    assert await users.count_since("2999-01-01 00:00:00") == 0


# ===== Подписки =====


async def test_activation_and_extension(subs) -> None:
    assert await subs.get_active(111) is None
    first = await subs.activate(111, "pro", 30, "stars", "c1")
    assert await subs.get_active(111) is not None
    second = await subs.activate(111, "pro", 30, "stars", "c2")
    assert second > first                       # продление суммируется
    assert await subs.count_active() == 1       # одна активная на пользователя


async def test_lifetime_is_never_shortened(subs) -> None:
    expires = await subs.activate(111, "pro", 0, "gift", lifetime=True)
    assert expires == LIFETIME_EXPIRES
    again = await subs.activate(111, "pro", 30, "gift")
    assert again == LIFETIME_EXPIRES


@pytest.mark.parametrize("value,expected", [
    (None, False),
    ("", False),
    ("2030-01-01 00:00:00", False),
    (LIFETIME_EXPIRES, True),
])
def test_is_lifetime(value, expected: bool) -> None:
    assert is_lifetime(value) is expected


async def test_cancel_returns_removed_row_and_reason(subs, db) -> None:
    assert await subs.cancel(111, "user") is None
    await subs.activate(111, "pro", 30, "stars", "c9")

    cancelled = await subs.cancel(111, "user")
    assert cancelled and cancelled["plan"] == "pro" and cancelled["expires_at"]
    assert await subs.get_active(111) is None
    assert await subs.cancel(111, "user") is None      # повторно нечего отменять

    async with aiosqlite.connect(str(db.db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT active, cancelled_at, cancel_reason FROM subscriptions "
            "WHERE charge_id = 'c9'"
        )
        row = await cur.fetchone()
    assert row["active"] == 0
    assert row["cancel_reason"] == "user"
    assert row["cancelled_at"]


async def test_cancel_marks_admin_reason(subs, db) -> None:
    await subs.activate(111, "pro", 30, "gift")
    await subs.cancel(111, "admin")
    async with aiosqlite.connect(str(db.db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT cancel_reason FROM subscriptions LIMIT 1")
        assert (await cur.fetchone())["cancel_reason"] == "admin"


async def test_cancel_ignores_expired(subs) -> None:
    await subs.activate(111, "pro", -1, "stars", "c1")   # уже истекла
    assert await subs.cancel(111, "user") is None


async def test_expire_due_marks_expired(subs, db) -> None:
    await subs.activate(111, "pro", -1, "stars", "c1")
    await subs.activate(222, "pro", 30, "stars", "c2")
    assert await subs.expire_due() == 1
    async with aiosqlite.connect(str(db.db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT cancel_reason FROM subscriptions WHERE charge_id = 'c1'"
        )
        assert (await cur.fetchone())["cancel_reason"] == "expired"
    assert await subs.count_active() == 1


# ===== Статистика использования =====


async def test_usage_counters(usage) -> None:
    for _ in range(3):
        await usage.add(111, "ok", "free", file_size=1_000_000, duration=10.0,
                        fmt=".mp4", processing_ms=1500)
    await usage.add(111, "error", "free", fmt=".mov", error="boom", processing_ms=500)

    assert await usage.count_all() == 4
    assert await usage.count_since("2000-01-01 00:00:00", status="error") == 1
    assert await usage.active_users_since("2000-01-01 00:00:00") == 1
    assert await usage.avg_processing_ms("2000-01-01 00:00:00") == 1500
    assert await usage.total_bytes("2000-01-01 00:00:00") == 3_000_000
    assert await usage.daily_counts(7)
    assert (await usage.top_formats("2000-01-01 00:00:00", 5))[0] == (".mp4", 3)


async def test_daily_limit_counts_only_successes(usage) -> None:
    for _ in range(3):
        await usage.add(111, "ok", "free", fmt=".mp4")
    await usage.add(111, "error", "free", fmt=".mov", error="boom")
    assert await usage.count_user_today(111) == 3      # ошибка лимит не съедает
    assert await usage.count_user_today(999) == 0


async def test_daily_limit_query_uses_index(db) -> None:
    async with aiosqlite.connect(str(db.db_path)) as conn:
        cur = await conn.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM usage "
            "WHERE user_id = 111 AND status = 'ok' AND ts >= date('now')"
        )
        plan = " ".join(row[3] for row in await cur.fetchall())
    assert "USING INDEX" in plan and "SCAN usage" not in plan, plan


async def test_stats_for_user(usage) -> None:
    for _ in range(3):
        await usage.add(111, "ok", "free", fmt=".mp4")
    await usage.add(111, "error", "free", fmt=".mov", error="boom")

    stats = await usage.stats_for_user(111)
    assert stats["ok"] == 3 and stats["errors"] == 1 and stats["today"] == 3
    assert stats["last_usage"]
    assert (await usage.stats_for_user(99999))["ok"] == 0      # нет данных — не падает


# ===== Настройки, канал, платежи =====


async def test_settings_bool_int_and_all(settings) -> None:
    assert await settings.get_bool("maintenance", False) is False
    await settings.set("maintenance", "true")
    assert await settings.get_bool("maintenance", False) is True
    assert await settings.get_int("nope", 42) == 42
    assert (await settings.all())["maintenance"] == "true"


async def test_channel_link(settings, channel) -> None:
    assert await channel.get_link() == ""
    await channel.set_link("@testchan")
    assert await channel.get_link() == "@testchan"
    await channel.set_link("")
    assert await channel.get_link() == ""


async def test_payment_is_idempotent_by_charge_id(payments) -> None:
    assert await payments.add(111, "ch1", "pro", 30, 100) is True
    assert await payments.add(111, "ch1", "pro", 30, 100) is False    # дубль
    assert await payments.count() == 1
    assert await payments.total_stars() == 100
