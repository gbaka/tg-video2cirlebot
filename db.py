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


class Database:
    """Обёртка над SQLite: единый путь, инициализация схемы, подключения."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    async def init(self) -> None:
        """Создаёт директорию и таблицы."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            # Гарантируем строку настроек канала
            await db.execute(
                "INSERT OR IGNORE INTO channel_settings (id, link) VALUES (1, '')"
            )
            await db.commit()
        logger.info("База данных готова: %s", self.db_path)

    def connect(self) -> aiosqlite.Connection:
        """Возвращает контекстный менеджер подключения (row_factory = Row)."""
        conn = aiosqlite.connect(self.db_path)
        return conn
