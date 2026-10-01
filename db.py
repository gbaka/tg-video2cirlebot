"""
SQLite: схема и подключение.

Все динамические данные (пользователи, подписки, статистика, настройки)
хранятся здесь. Статическая конфигурация — в config.yaml.
"""

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS issued_invoices (
    payload TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    plan TEXT NOT NULL,
    days INTEGER NOT NULL,
    stars INTEGER NOT NULL,
    lifetime INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS conversion_cache (
    cache_key TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    language    TEXT NOT NULL DEFAULT 'ru',
    quality     INTEGER,          -- выбранное разрешение кружка (для Pro)
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_users_created ON users(created_at);
CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    plan        TEXT NOT NULL,
    started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP NOT NULL,
    source      TEXT,             -- 'stars' | 'admin'
    charge_id   TEXT,             -- telegram_payment_charge_id
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id, active);
CREATE INDEX IF NOT EXISTS idx_sub_expires ON subscriptions(expires_at);

CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    ts            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    file_size     INTEGER,        -- байт
    duration      REAL,           -- сек
    format        TEXT,           -- расширение исходника
    status        TEXT,           -- 'ok' | 'error'
    error         TEXT,
    processing_ms INTEGER,
    plan          TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_user_ts ON usage(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_status_ts ON usage(status, ts);

CREATE TABLE IF NOT EXISTS bot_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channel_settings (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    link       TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    charge_id   TEXT UNIQUE,
    plan        TEXT,
    days        INTEGER,
    stars       INTEGER,
    ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_ts ON payments(ts);
"""

# Догоняющие миграции: (таблица, колонка, тип).
# Нужны для баз, созданных более ранними версиями схемы.
MIGRATIONS = [
    ("conversion_cache", "source_size", "INTEGER"),
    ("conversion_cache", "source_duration", "REAL"),
    ("conversion_cache", "output_duration", "REAL"),
    ("users", "crop_mode", "TEXT DEFAULT 'crop'"),
    ("users", "trim_start", "REAL DEFAULT 0"),
    ("users", "trim_duration", "REAL DEFAULT NULL"),
    ("payments", "applied_expires", "TEXT"),
    ("subscriptions", "cancelled_at", "TIMESTAMP"),
    ("subscriptions", "cancel_reason", "TEXT"),
]


class Database:
    """Обёртка над SQLite: единый путь, инициализация схемы, подключения."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    async def init(self) -> None:
        """Создаёт директорию, таблицы и применяет догоняющие миграции."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.execute("BEGIN IMMEDIATE")
            await self._migrate(db)
            # Retain longest entitlement, archive overlaps without deleting history.
            await db.execute(
                "UPDATE subscriptions SET active = 0, cancelled_at = CURRENT_TIMESTAMP, "
                "cancel_reason = 'migration_duplicate' WHERE active = 1 AND id NOT IN ("
                "SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id "
                "ORDER BY expires_at DESC, id DESC) AS rank FROM subscriptions WHERE active = 1) "
                "WHERE rank = 1)"
            )
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_one_active "
                "ON subscriptions(user_id) WHERE active = 1"
            )
            # Гарантируем строку настроек канала
            await db.execute("INSERT OR IGNORE INTO channel_settings (id, link) VALUES (1, '')")
            await db.commit()
        logger.info("База данных готова: %s", self.db_path)

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        """Добавляет недостающие колонки в существующие таблицы."""
        for table, column, coltype in MIGRATIONS:
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            if column not in existing:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                logger.info("Миграция: %s.%s добавлена", table, column)

    def connect(self) -> aiosqlite.Connection:
        """Возвращает контекстный менеджер подключения (row_factory = Row)."""
        conn = aiosqlite.connect(self.db_path)
        return conn
