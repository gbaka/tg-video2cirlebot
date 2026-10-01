"""Reject unsafe YAML settings before startup and invoicing."""

from pathlib import Path

import pytest
import yaml

from config_loader import Config


def load(tmp_path: Path, overrides: dict) -> Config:
    data = {"bot": {"token": "123:ABC", "admin_ids": [1]}, **overrides}
    path = tmp_path / "test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Config.load(path)


@pytest.mark.parametrize("overrides", [
    {"bot": {"token": "123:ABC", "admin_ids": ["123"]}},
    {"bot": {"token": "123:ABC", "admin_ids": [True]}},
    {"default_language": "xx"},
    {"logging": {"level": "NOTALEVEL"}},
    {"plans": {"free": {"daily_limit": -1}}},
    {"plans": {"free": {"default_resolution": 999}}},
    {"plans": {"free": {"resolutions": [361], "default_resolution": 361}}},
    {"plans": {"free": {"preset": "not-a-preset"}}},
    {"plans": {"free": {"crf": 70}}},
    {"plans": {"free": {"max_duration_sec": 61}}},
    {"prices": [{"code": "bad", "plan": "pro", "days": 0, "stars": 10}]},
    {"prices": [{"code": "bad", "plan": "unknown", "days": 30, "stars": 10}]},
    {"prices": [{"code": "bad", "plan": "pro", "days": 30, "stars": 0}]},
    {"prices": [
        {"code": "same", "plan": "pro", "days": 30, "stars": 10},
        {"code": "same", "plan": "pro", "days": 60, "stars": 20},
    ]},
])
def test_semantically_invalid_settings_report_errors(tmp_path: Path, overrides: dict) -> None:
    assert load(tmp_path, overrides).validate()


@pytest.mark.parametrize("overrides", [
    {"plans": {"free": {"daily_limit": "5"}}},
    {"prices": [{"code": "p", "plan": "pro", "days": 30, "stars": 10,
                 "lifetime": "false"}]},
    {"processing": {"workers": True}},
    {"processing": {"queue_size": "20"}},
    {"unknown_section": {}},
])
def test_invalid_yaml_types_raise_clear_error(tmp_path: Path, overrides: dict) -> None:
    with pytest.raises(ValueError):
        load(tmp_path, overrides)


def test_effective_download_limit_is_independent_of_tariff(tmp_path: Path) -> None:
    config = load(tmp_path, {})
    assert config.api.input_limit_bytes(config.plans.pro.max_size_bytes) == 20 * 1024 * 1024


def test_local_api_preserves_tariff_limit(tmp_path: Path) -> None:
    config = load(tmp_path, {
        "api": {"base_url": "http://bot-api:8081", "local": True},
        "plans": {"pro": {"max_size_mb": 200}},
    })
    assert not config.validate()
    assert config.api.input_limit_bytes(config.plans.pro.max_size_bytes) == 200 * 1024 * 1024


def test_local_api_with_no_plan_limit_leaves_no_limit(tmp_path: Path) -> None:
    """Pro без ограничения: локальный API не ставит свой потолок."""
    config = load(tmp_path, {"api": {"base_url": "http://bot-api:8081", "local": True}})
    assert config.plans.pro.max_size_mb == 0
    assert config.api.input_limit_bytes(config.plans.pro.max_size_bytes) == 0


def test_free_plan_keeps_a_limit_below_the_cloud_cap(tmp_path: Path) -> None:
    """15 MB меньше облачных 20 MB — значит лимит тарифа и применяется."""
    config = load(tmp_path, {})
    assert config.plans.free.max_size_mb == 15
    assert config.api.input_limit_bytes(config.plans.free.max_size_bytes) == 15 * 1024 * 1024


def test_ineffective_size_limit_is_reported_as_a_warning(tmp_path: Path) -> None:
    """Лимит выше облачного потолка не действует — предупреждаем, но не падаем."""
    config = load(tmp_path, {"plans": {"pro": {"max_size_mb": 200}}})
    assert not config.validate()
    notes = config.warnings()
    assert len(notes) == 1 and "plans.pro.max_size_mb" in notes[0], notes
    assert "200" in notes[0] and "20" in notes[0]


def test_current_plan_limits_produce_no_warnings(tmp_path: Path) -> None:
    config = load(tmp_path, {})
    assert config.warnings() == []


def test_local_api_makes_a_big_plan_limit_effective_and_silent(tmp_path: Path) -> None:
    config = load(tmp_path, {
        "api": {"base_url": "http://bot-api:8081", "local": True},
        "plans": {"pro": {"max_size_mb": 200}},
    })
    assert config.warnings() == []


@pytest.mark.parametrize("api", [
    {"base_url": "http://example.com", "local": False},
    {"base_url": "", "local": True},
    {"base_url": "ftp://bot-api", "local": True},
    {"base_url": "http://user:password@bot-api", "local": True},
])
def test_invalid_api_settings_report_errors(tmp_path: Path, api: dict) -> None:
    assert load(tmp_path, {"api": api}).validate()


@pytest.mark.parametrize("processing", [
    {"workers": 0}, {"queue_size": 0}, {"probe_timeout_sec": 0},
    {"encode_timeout_sec": -1}, {"shutdown_timeout_sec": 0}, {"temp_dir": "/"},
])
def test_invalid_processing_settings_report_errors(tmp_path: Path, processing: dict) -> None:
    assert load(tmp_path, {"processing": processing}).validate()


@pytest.mark.parametrize("raw", ["false", "[]", "42"])
def test_non_mapping_root_is_rejected(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(raw)
    with pytest.raises(ValueError, match="mapping"):
        Config.load(path)


@pytest.mark.parametrize("base_url", ["http://[broken", "http://bot-api:bad", "http://bot api"])
def test_malformed_local_api_is_rejected(tmp_path: Path, base_url: str) -> None:
    config = load(tmp_path, {"api": {"local": True, "base_url": base_url}})
    assert config.validate()


@pytest.mark.parametrize("price", [
    {"code": "test", "plan": "pro", "days": 30, "stars": 10**10},
    {"code": "test", "plan": "pro", "days": 365001, "stars": 50, "lifetime": True},
    {"code": "test", "plan": "free", "days": 30, "stars": 50},
])
def test_prices_are_encodable_and_only_sell_pro(tmp_path: Path, price: dict) -> None:
    assert load(tmp_path, {"prices": [price]}).validate()


def test_admin_id_outside_telegram_range_is_rejected(tmp_path: Path) -> None:
    assert load(tmp_path, {"bot": {"token": "123:ABC", "admin_ids": [2**52]}}).validate()
