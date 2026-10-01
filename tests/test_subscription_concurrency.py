"""Real SQLite regressions for serialized subscription and user updates."""

import asyncio
from datetime import datetime, timedelta

import aiosqlite
import pytest

from repositories import LIFETIME_EXPIRES


async def test_concurrent_renewals_do_not_lose_days(subs, monkeypatch):
    first = await subs.activate(42, "pro", 30, "gift")
    execute = aiosqlite.Connection.execute

    async def delayed_select(conn, sql, parameters=()):
        result = await execute(conn, sql, parameters)
        if sql.startswith("SELECT expires_at"):
            await asyncio.sleep(0.05)
        return result

    monkeypatch.setattr(aiosqlite.Connection, "execute", delayed_select)
    await asyncio.gather(*(subs.activate(42, "pro", 10, "gift") for _ in range(3)))
    row = await subs.get_active(42)
    assert datetime.fromisoformat(row["expires_at"]) == (
        datetime.fromisoformat(first) + timedelta(days=30)
    )


async def test_concurrent_initial_grants_and_lifetime(subs, db):
    await asyncio.gather(
        *(subs.activate(77, "pro", 10, "gift", lifetime=(i == 1)) for i in range(6))
    )
    assert (await subs.get_active(77))["expires_at"] == LIFETIME_EXPIRES
    async with db.connect() as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id=77 AND active=1")
        assert (await cur.fetchone())[0] == 1


async def test_concurrent_first_user_updates_are_atomic(users):
    rows = await asyncio.gather(*(users.get_or_create(77, str(i), "Name") for i in range(8)))
    assert len(rows) == 8 and await users.count_all() == 1
    await users.set_language(77, "en")
    row = await users.get_or_create(77, "new", "New", "ru")
    assert row["username"] == "new" and row["first_name"] == "New"
    assert row["language"] == "en"


async def test_video_options_and_fragment_reset(users):
    row = await users.get_or_create(77, None, None)
    assert (row["crop_mode"], row["trim_start"], row["trim_duration"]) == ("crop", 0, None)
    await users.set_video_options(77, crop_mode="fit", trim_start=2.5, trim_duration=12.0)
    await users.reset_video_fragment(77)
    row = await users.get(77)
    assert (row["crop_mode"], row["trim_start"], row["trim_duration"]) == ("fit", 0, None)


async def test_duplicate_migration_keeps_longest_and_enforces_index(db):
    async with db.connect() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_sub_one_active")
        for expires in ["2030-01-01 00:00:00", LIFETIME_EXPIRES, "2040-01-01 00:00:00"]:
            await conn.execute(
                "INSERT INTO subscriptions(user_id,plan,expires_at) VALUES(88,'pro',?)", (expires,)
            )
        await conn.commit()
    await db.init()
    await db.init()
    async with db.connect() as conn:
        cur = await conn.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id=88 AND active=1"
        )
        assert await cur.fetchall() == [(LIFETIME_EXPIRES,)]
        cur = await conn.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id=88")
        assert (await cur.fetchone())[0] == 3
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO subscriptions(user_id,plan,expires_at) "
                "VALUES(88,'pro','2050-01-01 00:00:00')"
            )


async def test_conversion_cache_schema(db):
    async with db.connect() as conn:
        await conn.execute("INSERT INTO conversion_cache(cache_key,file_id) VALUES ('key','id')")
        cur = await conn.execute(
            "SELECT file_id,created_at FROM conversion_cache WHERE cache_key='key'"
        )
        row = await cur.fetchone()
        assert row[0] == "id" and row[1]


async def test_video_fragment_nullable_update(users):
    await users.get_or_create(42, None, None)
    await users.set_video_options(42, trim_duration=10)
    await users.set_video_options(42, crop_mode="fit")
    assert (await users.get(42))["trim_duration"] == 10
    await users.set_video_options(42, trim_duration=None)
    assert (await users.get(42))["trim_duration"] is None


async def test_cancel_and_lifetime_grant_are_serialized(subs, monkeypatch):
    await subs.activate(42, "pro", 10, "gift")
    execute = aiosqlite.Connection.execute
    selected = asyncio.Event()
    release = asyncio.Event()

    async def held_select(conn, sql, parameters=()):
        result = await execute(conn, sql, parameters)
        if sql.startswith("SELECT * FROM subscriptions"):
            assert conn.in_transaction, "Cancellation read must own a write transaction"
            selected.set()
            await release.wait()
        return result

    monkeypatch.setattr(aiosqlite.Connection, "execute", held_select)
    cancellation = asyncio.create_task(subs.cancel(42))
    await selected.wait()
    grant = asyncio.create_task(subs.activate(42, "pro", 0, "gift", lifetime=True))
    await asyncio.sleep(0.02)
    assert not grant.done()
    release.set()
    cancelled, expires = await asyncio.gather(cancellation, grant)
    assert cancelled and expires == LIFETIME_EXPIRES
    monkeypatch.setattr(aiosqlite.Connection, "execute", execute)
    assert (await subs.get_active(42))["expires_at"] == LIFETIME_EXPIRES
