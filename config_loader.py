"""
Загрузка конфигурации бота.
Единственный источник статических настроек — config.yaml (без ENV).
Динамика (подписки, статистика, ссылка на канал) — в SQLite.
"""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
    "api": {"base_url": "", "local": False},
    "processing": {
        "workers": 1, "queue_size": 20, "probe_timeout_sec": 15,
        "encode_timeout_sec": 120, "shutdown_timeout_sec": 30, "temp_dir": "data/tmp",
    },
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
class APIConfig:
    base_url: str = ""
    local: bool = False

    def input_limit_bytes(self, plan_limit: int) -> int:
        """Cloud getFile cap is independent of subscription limits."""
        if self.local:
            return plan_limit
        cloud_limit = 20 * 1024 * 1024
        return min(plan_limit, cloud_limit) if plan_limit > 0 else cloud_limit


@dataclass
class ProcessingConfig:
    workers: int = 1
    queue_size: int = 20
    probe_timeout_sec: float = 15
    encode_timeout_sec: float = 120
    shutdown_timeout_sec: float = 30
    temp_dir: str = "data/tmp"


@dataclass
class Config:
    bot: BotConfig
    channel: ChannelConfig
    logging: LoggingConfig
    plans: Plans
    default_language: str = "ru"
    api: APIConfig = field(default_factory=APIConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "Config":
        """
        Загружает конфиг.

        Без аргумента — значения по умолчанию (нужно тестам). С путём файл
        обязан существовать: молчаливая подстановка умолчаний вместо
        отсутствующего конфига даёт ложную диагностику — «bot.token не задан»
        вместо «config.yaml не найден».
        """
        data = _deep_copy(DEFAULTS)

        if config_path is not None:
            path = Path(config_path)
            if not path.is_file():
                raise FileNotFoundError(f"Не найден файл конфигурации: {path}")
            with open(path, encoding="utf-8") as f:
                try:
                    yaml_data = yaml.safe_load(f)
                    if yaml_data is None:
                        yaml_data = {}
                except yaml.YAMLError:
                    raise ValueError("Некорректный YAML в конфигурации") from None
                _check_types(yaml_data)
                _merge(data, yaml_data)

        plans = _build_plans(data)

        return cls(
            bot=BotConfig(**data["bot"]),
            channel=ChannelConfig(**data["channel"]),
            logging=LoggingConfig(**data["logging"]),
            plans=plans,
            default_language=data.get("default_language", "ru"),
            api=APIConfig(**data["api"]),
            processing=ProcessingConfig(**data["processing"]),
        )

    def validate(self) -> list[str]:
        errors = []
        if not self.bot.token:
            errors.append("bot.token не задан в config.yaml")
        elif (not isinstance(self.bot.token, str)
              or not re.fullmatch(r"[0-9]+:[^\s:]+", self.bot.token)):
            errors.append("bot.token: ожидается токен BotFather")
        if not self.bot.admin_ids:
            errors.append("bot.admin_ids не заданы в config.yaml (нужен хотя бы один админ)")
        elif any(type(uid) is not int or not 0 < uid < 2**52 for uid in self.bot.admin_ids):
            errors.append("bot.admin_ids: ожидаются положительные целочисленные ID")
        if self.default_language not in {"ru", "en"}:
            errors.append("default_language: допустимы ru и en")
        if self.logging.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append("logging.level: неизвестный уровень")
        presets = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
                   "slow", "slower", "veryslow"}
        for plan in (self.plans.free, self.plans.pro):
            prefix = f"plans.{plan.code}"
            if any(type(r) is not int or r < 2 or r > 640 or r % 2 for r in plan.resolutions):
                errors.append(f"{prefix}.resolutions: чётные размеры от 2 до 640")
            if not plan.resolutions or plan.default_resolution not in plan.resolutions:
                errors.append(f"{prefix}.default_resolution: должен входить в resolutions")
            if not 1 <= plan.max_duration_sec <= 60:
                errors.append(f"{prefix}.max_duration_sec: от 1 до 60")
            if any(v < 0 for v in (plan.max_size_mb, plan.daily_limit, plan.max_album)):
                errors.append(f"{prefix}: лимиты не могут быть отрицательными")
            if not 0 <= plan.crf <= 51:
                errors.append(f"{prefix}.crf: от 0 до 51")
            if plan.preset not in presets:
                errors.append(f"{prefix}.preset: неизвестный preset x264")
        codes = set()
        for price in self.plans.prices:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", price.code) or price.code in codes:
                errors.append("prices.code: уникальный код из букв, цифр, '_' и '-' (до 40)")
            codes.add(price.code)
            if price.plan != "pro":
                errors.append(f"prices.{price.code}.plan: продаётся только pro")
            if price.stars <= 0 or price.stars > 10000:
                errors.append(f"prices.{price.code}.stars: от 1 до 10000")
            if not price.lifetime and not 1 <= price.days <= 36500:
                errors.append(f"prices.{price.code}.days: от 1 до 36500 для срочной подписки")
            if price.lifetime and not 0 <= price.days <= 365000:
                errors.append(f"prices.{price.code}.days: ожидается 0..365000")
        if self.api.local:
            try:
                url = urlsplit(self.api.base_url)
                port = url.port
                valid_api = (url.scheme in {"http", "https"} and bool(url.hostname)
                             and not any(ch.isspace() for ch in self.api.base_url)
                             and not url.username and not url.password
                             and not url.query and not url.fragment and url.path in {"", "/"}
                             and (port is None or 1 <= port <= 65535))
            except ValueError:
                valid_api = False
            if not valid_api:
                errors.append("api.base_url: HTTP(S) origin локального Bot API без credentials")
        elif self.api.base_url:
            errors.append("api.base_url: задавайте только вместе с api.local: true")
        for name in ("workers", "queue_size", "probe_timeout_sec", "encode_timeout_sec",
                     "shutdown_timeout_sec"):
            value = getattr(self.processing, name)
            if not math.isfinite(value) or value <= 0:
                errors.append(f"processing.{name}: положительное конечное число")
        temp = Path(self.processing.temp_dir)
        if not self.processing.temp_dir or str(temp) in {"/", ".", "/tmp", "/var/tmp"}:
            errors.append("processing.temp_dir: нужен выделенный каталог бота")
        return errors


def _check_types(data: Any) -> None:
    """Reject coercions and misspelled keys without echoing secret values."""
    if not isinstance(data, dict):
        raise ValueError("Конфигурация: ожидается YAML mapping")
    schemas: dict[str, dict[str, type | tuple[type, ...]]] = {
        "bot": {"token": str, "admin_ids": list},
        "channel": {"invite_text": str},
        "logging": {"level": str},
        "api": {"base_url": str, "local": bool},
        "processing": {
            "workers": int, "queue_size": int, "probe_timeout_sec": (int, float),
            "encode_timeout_sec": (int, float), "shutdown_timeout_sec": (int, float),
            "temp_dir": str,
        },
    }
    plan_schema: dict[str, type | tuple[type, ...]] = {
        "max_size_mb": int, "max_duration_sec": int, "resolutions": list,
        "default_resolution": int, "crf": int, "preset": str,
        "daily_limit": int, "max_album": int,
    }
    price_schema: dict[str, type | tuple[type, ...]] = {
        "code": str, "plan": str, "days": int, "stars": int, "lifetime": bool,
    }

    def check_mapping(value: Any, schema: dict, prefix: str) -> None:
        if not isinstance(value, dict):
            raise ValueError(f"{prefix}: ожидается mapping")
        for key, item in value.items():
            if key not in schema:
                raise ValueError(f"{prefix}: неизвестное поле")
            expected = schema[key]
            types = expected if isinstance(expected, tuple) else (expected,)
            if type(item) not in types:
                raise ValueError(f"{prefix}.{key}: неверный тип YAML")

    if set(data) - set(DEFAULTS):
        raise ValueError("Конфигурация: неизвестный раздел")
    for section, schema in schemas.items():
        if section in data:
            check_mapping(data[section], schema, section)
    if "default_language" in data and not isinstance(data["default_language"], str):
        raise ValueError("default_language: ожидается строка")
    if "plans" in data:
        if not isinstance(data["plans"], dict) or set(data["plans"]) - {"free", "pro"}:
            raise ValueError("plans: допустимы free и pro")
        for code, cfg in data["plans"].items():
            check_mapping(cfg, plan_schema, f"plans.{code}")
            if any(type(r) is not int for r in cfg.get("resolutions", [])):
                raise ValueError(f"plans.{code}.resolutions: ожидаются целые числа")
    if "prices" in data:
        if not isinstance(data["prices"], list):
            raise ValueError("prices: ожидается список")
        for price in data["prices"]:
            check_mapping(price, price_schema, "prices")
            if not {"code", "plan", "stars"} <= set(price):
                raise ValueError("prices: обязательны code, plan и stars")


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
