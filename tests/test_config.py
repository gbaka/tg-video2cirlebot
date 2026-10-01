"""Конфигурация: загрузка YAML, значения по умолчанию, валидация."""

from pathlib import Path

import pytest
import yaml

from config_loader import Config


def write_config(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_loads_token_and_admins(tmp_path: Path) -> None:
    path = write_config(tmp_path / "c.yaml", {
        "bot": {"token": "123:ABC", "admin_ids": [1, 2]},
    })
    config = Config.load(str(path))
    assert config.bot.token == "123:ABC"
    assert config.bot.admin_ids == [1, 2]
    assert config.validate() == []


def test_missing_file_raises(tmp_path: Path) -> None:
    """
    Отсутствующий конфиг — громкая ошибка.

    Тихая подстановка умолчаний давала ложную диагностику: «bot.token не
    задан» вместо «config.yaml не найден».
    """
    with pytest.raises(FileNotFoundError, match="файл конфигурации"):
        Config.load(str(tmp_path / "absent.yaml"))


def test_defaults_without_path() -> None:
    """Без пути — умолчания (это то, что нужно тестам и встроенному примеру)."""
    config = Config.load()
    assert config.bot.admin_ids == []
    assert config.default_language == "ru"
    assert config.plans.free.daily_limit == 5


def test_validate_reports_missing_fields(tmp_path: Path) -> None:
    path = write_config(tmp_path / "c.yaml", {"bot": {"token": "", "admin_ids": []}})
    config = Config.load(str(path))
    errors = config.validate()
    assert len(errors) == 2
    assert any("token" in e for e in errors)
    assert any("admin_ids" in e for e in errors)


def test_yaml_overrides_only_given_fields(tmp_path: Path) -> None:
    path = write_config(tmp_path / "c.yaml", {
        "bot": {"token": "1:A", "admin_ids": [1]},
        "plans": {"free": {"daily_limit": 99}},
    })
    config = Config.load(str(path))
    assert config.plans.free.daily_limit == 99
    assert config.plans.free.max_size_mb == 15          # остальное из DEFAULTS
    assert config.plans.free.max_album == 3


def test_plans_and_prices_are_built(tmp_path: Path) -> None:
    path = write_config(tmp_path / "c.yaml", {
        "bot": {"token": "1:A", "admin_ids": [1]},
        "prices": [
            {"code": "a", "plan": "pro", "days": 30, "stars": 10},
            {"code": "b", "plan": "pro", "days": 0, "stars": 20, "lifetime": True},
        ],
    })
    config = Config.load(str(path))
    assert config.plans.free.code == "free"
    assert config.plans.pro.code == "pro"
    assert [p.code for p in config.plans.prices] == ["a", "b"]
    assert config.plans.prices[1].lifetime is True
    assert config.plans.price("b").stars == 20


def test_resolutions_fall_back_to_default(tmp_path: Path) -> None:
    path = write_config(tmp_path / "c.yaml", {
        "bot": {"token": "1:A", "admin_ids": [1]},
        "plans": {"free": {"resolutions": [], "default_resolution": 480}},
    })
    config = Config.load(str(path))
    assert config.plans.free.resolutions == [480]


def test_example_config_is_valid() -> None:
    """config.example.yaml из репозитория должен читаться без ошибок."""
    root = Path(__file__).resolve().parent.parent
    config = Config.load(str(root / "config.example.yaml"))
    assert config.plans.prices
    assert config.plans.free.max_album >= 1
    assert config.validate() == []
    assert config.warnings() == []


def _leaf_paths(data: dict, prefix: str = "") -> dict[str, object]:
    """Плоский список «путь → значение» для вложенных словарей и списков словарей."""
    flat: dict[str, object] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_leaf_paths(value, path))
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for index, item in enumerate(value):
                flat.update(_leaf_paths(item, f"{path}[{index}]"))
        else:
            flat[path] = value
    return flat


def test_example_config_mirrors_the_shipped_defaults() -> None:
    """Каждая настройка из DEFAULTS описана в примере и совпадает по значению.

    Иначе новый ключ появляется в коде и не попадает в шаблон, а старый в
    примере начинает расходиться с реальным поведением — именно так в примере
    оказалось «max_size_mb: 200», которого в коде уже не было.
    """
    from config_loader import DEFAULTS

    root = Path(__file__).resolve().parent.parent
    example = yaml.safe_load((root / "config.example.yaml").read_text(encoding="utf-8"))
    documented = _leaf_paths(example)
    defaults = _leaf_paths(DEFAULTS)
    # Плейсхолдеры заполняет оператор, в DEFAULTS их нет.
    skip = {"bot.token", "bot.admin_ids"}
    missing = sorted(p for p in defaults if p not in documented and p not in skip)
    assert not missing, f"не описаны в config.example.yaml: {missing}"
    for path, value in defaults.items():
        if path in skip:
            continue
        assert documented[path] == value, (
            f"{path}: пример {documented[path]!r}, DEFAULTS {value!r}"
        )


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_log_level_passthrough(tmp_path: Path, level: str) -> None:
    path = write_config(tmp_path / "c.yaml", {
        "bot": {"token": "1:A", "admin_ids": [1]},
        "logging": {"level": level},
    })
    assert Config.load(str(path)).logging.level == level
