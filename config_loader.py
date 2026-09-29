"""
Загрузка и управление конфигурацией бота.
Только YAML конфиг (единственный источник настроек, без ENV).
Динамические настройки (ссылка на канал) — в SQLite (storage.py).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class BotConfig:
    token: str
    admin_ids: List[int] = field(default_factory=list)


@dataclass
class ChannelConfig:
    # link теперь хранится в SQLite, здесь только invite_text
    invite_text: str = "Вступить в канал"


@dataclass
class VideoConfig:
    max_size_mb: int = 50
    target_resolution: int = 360
    crf: int = 24
    preset: str = "fast"


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    bot: BotConfig
    channel: ChannelConfig
    video: VideoConfig
    logging: LoggingConfig

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Config":
        """Загружает конфигурацию из YAML файла."""
        # Базовые значения по умолчанию
        data = {
            "bot": {"token": "", "admin_ids": []},
            "channel": {"invite_text": "Вступить в канал"},
            "video": {"max_size_mb": 50, "target_resolution": 360, "crf": 24, "preset": "fast"},
            "logging": {"level": "INFO"}
        }

        # Читаем YAML если указан
        if config_path and Path(config_path).exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                yaml_data = yaml.safe_load(f) or {}
                for key, value in yaml_data.items():
                    if key in data and isinstance(value, dict):
                        data[key].update(value)
                    else:
                        data[key] = value

        return cls(
            bot=BotConfig(**data["bot"]),
            channel=ChannelConfig(**data["channel"]),
            video=VideoConfig(**data["video"]),
            logging=LoggingConfig(**data["logging"])
        )

    def validate(self) -> List[str]:
        """Проверяет конфигурацию, возвращает список ошибок."""
        errors = []
        if not self.bot.token:
            errors.append("BOT_TOKEN не задан в config.yaml")
        if not self.bot.admin_ids:
            errors.append("admin_ids не заданы в config.yaml (нужен хотя бы один админ)")
        return errors