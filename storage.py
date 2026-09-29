"""
SQLite хранилище для динамических настроек (ссылка на канал).
"""

import aiosqlite
from pathlib import Path


class ChannelStorage:
    """Хранилище ссылки на канал/чат в SQLite."""

    def __init__(self, db_path: str = "bot_data.db"):
        self.db_path = Path(db_path)

    async def init(self) -> None:
        """Инициализирует таблицу."""
        # Создаём директорию если нужно
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS channel_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    link TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Гарантируем наличие строки
            await db.execute("""
                INSERT OR IGNORE INTO channel_settings (id, link) VALUES (1, '')
            """)
            await db.commit()

    async def get_link(self) -> str:
        """Получает текущую ссылку на канал."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT link FROM channel_settings WHERE id = 1")
            row = await cursor.fetchone()
            return row[0] if row else ""

    async def set_link(self, link: str) -> None:
        """Сохраняет новую ссылку на канал."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE channel_settings SET link = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                (link,)
            )
            await db.commit()

    async def clear_link(self) -> None:
        """Очищает ссылку на канал."""
        await self.set_link("")