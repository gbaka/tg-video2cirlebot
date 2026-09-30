"""Схема БД: таблицы, индексы, догоняющие миграции."""

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from db import MIGRATIONS, Database

EXPECTED_TABLES = {
    "users", "subscriptions", "usage", "bot_settings", "channel_settings", "payments",
}


async def _tables(path: str | Path) -> set[str]:
    async with aiosqlite.connect(str(path)) as conn:
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in await cur.fetchall()}


async def _columns(path: str | Path, table: str) -> set[str]:
    async with aiosqlite.connect(str(path)) as conn:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in await cur.fetchall()}


async def _indexes(path: str | Path) -> set[str]:
    async with aiosqlite.connect(str(path)) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
        )
        return {row[0] for row in await cur.fetchall()}


async def test_init_creates_tables(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    await Database(str(path)).init()
    assert await _tables(path) >= EXPECTED_TABLES


async def test_init_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    db = Database(str(path))
    await db.init()
    await db.init()                      # повторный запуск не должен падать
    assert await _tables(path) >= EXPECTED_TABLES


async def test_init_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "t.db"
    await Database(str(path)).init()
    assert path.exists()


async def test_indexes_present(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    await Database(str(path)).init()
    assert {
        "idx_users_created", "idx_users_last_seen", "idx_users_username",
        "idx_sub_user", "idx_sub_expires",
        "idx_usage_ts", "idx_usage_user", "idx_usage_user_ts", "idx_usage_status_ts",
        "idx_payments_user", "idx_payments_ts",
    } <= await _indexes(path)


async def test_migrations_apply_to_old_database(tmp_path: Path) -> None:
    """База без новых колонок должна догонять схему при старте."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            source TEXT,
            charge_id TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.commit()
    conn.close()

    before = await _columns(path, "subscriptions")
    assert "cancel_reason" not in before

    await Database(str(path)).init()

    after = await _columns(path, "subscriptions")
    for table, column, _ in MIGRATIONS:
        if table == "subscriptions":
            assert column in after, f"{column} не добавлена"


@pytest.mark.parametrize("table,column", [(t, c) for t, c, _ in MIGRATIONS])
def test_migrations_are_declared(table: str, column: str) -> None:
    assert table and column
