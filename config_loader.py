"""
Загрузка конфигурации бота.
Единственный источник статических настроек — config.yaml (без ENV).
Динамика (подписки, статистика, ссылка на канал) — в SQLite.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from plans import Plan, Plans, PriceOption

DEFAULTS: dict[str, Any] = {
    "bot": {"token": "", "admin_ids": []},
    "default_language": "ru",
    "channel": {"invite_text": "Вступить в канал"},
    "plans": {
        "free": {
            "max_size_mb": 50, "max_duration_sec": 60, "resolutions": [360],
            "default_resolution": 360, "crf": 24, "preset": "fast",
            "daily_limit": 5, "max_album": 3,
        },
        "pro": {
            "max_size_mb": 200, "max_duration_sec": 60, "resolutions": [360, 480, 640],
            "default_resolution": 480, "crf": 20, "preset": "medium",
            "daily_limit": 0, "max_album": 0,
        },
    },
    "prices": [
        {"code": "pro_30d", "plan": "pro", "days": 30, "stars": 25},
        {"code": "pro_90d", "plan": "pro", "days": 90, "stars": 60},
        {"code": "pro_life", "plan": "pro", "days": 0, "stars": 150, "lifetime": True},
    ],
    "logging": {"level": "INFO"},
}


@dataclass
class BotConfig:
    token: str
    admin_ids: list[int] = field(default_factory=list)


@dataclass
class ChannelConfig:
    invite_text: str = "Вступить в канал"


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    bot: BotConfig
    channel: ChannelConfig
    logging: LoggingConfig
    plans: Plans
    default_language: str = "ru"

    @classmethod
    def load(cls, config_path: str | None = None) -> "Config":
        data = _deep_copy(DEFAULTS)

        if config_path and Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
                _merge(data, yaml_data)

        plans = _build_plans(data)

        return cls(
            bot=BotConfig(**data["bot"]),
            channel=ChannelConfig(**data["channel"]),
            logging=LoggingConfig(**data["logging"]),
            plans=plans,
            default_language=data.get("default_language", "ru"),
        )

    def validate(self) -> list[str]:
        errors = []
        if not self.bot.token:
            errors.append("bot.token не задан в config.yaml")
        if not self.bot.admin_ids:
            errors.append("bot.admin_ids не заданы в config.yaml (нужен хотя бы один админ)")
        return errors


def _build_plans(data: dict) -> Plans:
    def make(code: str, cfg: dict) -> Plan:
        resolutions = list(cfg.get("resolutions") or [cfg.get("default_resolution", 360)])
        return Plan(
            code=code,
            max_size_mb=int(cfg.get("max_size_mb", 50)),
            max_duration_sec=int(cfg.get("max_duration_sec", 60)),
            resolutions=[int(r) for r in resolutions],
            default_resolution=int(cfg.get("default_resolution", resolutions[0])),
            crf=int(cfg.get("crf", 24)),
            preset=str(cfg.get("preset", "fast")),
            daily_limit=int(cfg.get("daily_limit", 0)),
            max_album=int(cfg.get("max_album", 1)),
        )

    free = make("free", data["plans"]["free"])
    pro = make("pro", data["plans"]["pro"])

    prices = []
    for p in data.get("prices", []):
        prices.append(PriceOption(
            code=str(p["code"]), plan=str(p["plan"]),
            days=int(p.get("days", 0)), stars=int(p["stars"]),
            lifetime=bool(p.get("lifetime", False)),
        ))
    return Plans(free=free, pro=pro, prices=prices)


def _deep_copy(d: dict) -> dict:
    import copy
    return copy.deepcopy(d)


def _merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _merge(base[k], v)
        else:
            base[k] = v
