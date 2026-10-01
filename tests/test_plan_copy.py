"""Тексты про тарифы: цифры берутся из конфига, а не из локали."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import i18n
from config_loader import APIConfig
from plan_copy import limit_bytes, pro_benefits, size_limit_key, size_text, tariff_summary
from plans import Plan, Plans

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True, scope="module")
def _locales():
    i18n.load_locales(ROOT / "locales")


def make_ctx(free: Plan, pro: Plan, *, local: bool = False):
    return SimpleNamespace(
        config=SimpleNamespace(api=APIConfig(local=local)),
        plans=Plans(free, pro, []),
    )


def free_plan() -> Plan:
    return Plan("free", 20, 60, [360], 360, 24, "fast", 5, 3)


def pro_plan() -> Plan:
    return Plan("pro", 20, 60, [360, 480, 640], 480, 20, "medium", 0, 0)


def test_benefits_spell_out_the_real_limits():
    text = pro_benefits("ru", make_ctx(free_plan(), pro_plan()))
    assert "640×640" in text and "360×360" in text, text
    assert "конвертаций в день" in text and "5" in text, text
    assert i18n.t("ru", "sub.benefit_album_unlimited") in text, text
    assert i18n.t("ru", "sub.pro_benefits_title") in text


def test_benefits_follow_the_config_not_the_text():
    """Поменяли лимит в конфиге — поменялся и текст."""
    big = Plan("pro", 200, 60, [360, 480, 720, 1080], 720, 20, "medium", 0, 0)
    ctx = make_ctx(free_plan(), big, local=True)
    text = pro_benefits("ru", ctx)
    assert "1080×1080" in text, text
    assert "200 MB" in text and "вместо 20 MB" in text, text


def test_equal_limits_are_not_advertised():
    """Не обещаем то, чего тариф не даёт: одинаковые лимиты не упоминаются."""
    free = free_plan()
    same = Plan("pro", 20, 60, [360], 360, 20, "medium", 5, 3)
    assert pro_benefits("ru", make_ctx(free, same)) == ""


def test_cloud_cap_hides_a_size_difference_that_does_not_exist():
    """Free 20 MB и Pro 200 MB через облачный API дают одинаковые 20 MB."""
    pro = Plan("pro", 200, 60, [360, 480], 480, 20, "medium", 0, 0)
    text = pro_benefits("ru", make_ctx(free_plan(), pro))
    assert "MB" not in text, f"обещан недостижимый размер файла: {text}"
    assert "480×480" in text


def test_album_line_switches_when_pro_is_unlimited():
    unlimited = Plan("pro", 20, 60, [480], 480, 20, "medium", 0, 0)
    text = pro_benefits("ru", make_ctx(free_plan(), unlimited))
    assert i18n.t("ru", "sub.benefit_album_unlimited") in text


def test_limited_album_line_names_both_numbers():
    limited = Plan("pro", 20, 60, [480], 480, 20, "medium", 0, 5)
    text = pro_benefits("ru", make_ctx(free_plan(), limited))
    assert "5 видео" in text and "3" in text, text


@pytest.mark.parametrize("lang", i18n.SUPPORTED)
def test_tariff_summary_is_concrete_in_every_language(lang: str):
    text = tariff_summary(lang, make_ctx(free_plan(), pro_plan()))
    assert "360" in text and "640" in text, text
    assert "20" in text, text
    assert i18n.t(lang, "sub.limit_none") in text, text


def test_tariff_summary_marks_unlimited_plans():
    free = Plan("free", 20, 60, [360], 360, 24, "fast", 0, 3)
    text = tariff_summary("ru", make_ctx(free, pro_plan()))
    assert text.count(i18n.t("ru", "sub.limit_none")) == 2, text


def free_15() -> Plan:
    """Free после ограничения в 15 MB."""
    return Plan("free", 15, 60, [360], 360, 24, "fast", 5, 3)


def pro_unlimited() -> Plan:
    """Pro без ограничения по размеру (0): потолок остаётся только у Bot API."""
    return Plan("pro", 0, 60, [360, 480, 640], 480, 20, "medium", 0, 0)


def test_free_plan_size_comes_from_the_config() -> None:
    ctx = make_ctx(free_15(), pro_unlimited())
    assert size_text("ru", ctx, free_15()) == "15 MB"


def test_unlimited_plan_size_is_capped_by_the_cloud_api() -> None:
    """Без local API «без ограничения» означает ровно облачные 20 MB."""
    ctx = make_ctx(free_15(), pro_unlimited())
    assert limit_bytes(ctx, pro_unlimited()) == 20 * 1024 * 1024
    assert size_text("ru", ctx, pro_unlimited()) == "20 MB"


def test_unlimited_plan_size_with_local_api_is_not_capped() -> None:
    ctx = make_ctx(free_15(), pro_unlimited(), local=True)
    assert limit_bytes(ctx, pro_unlimited()) == 0
    assert size_text("ru", ctx, pro_unlimited()) == i18n.t("ru", "sub.limit_none")


def test_cloud_cap_size_line_names_both_plans() -> None:
    text = pro_benefits("ru", make_ctx(free_15(), pro_unlimited()))
    assert "20 MB" in text and "вместо 15 MB" in text, text


def test_limitless_pro_size_is_advertised_with_the_free_number() -> None:
    text = pro_benefits("ru", make_ctx(free_15(), pro_unlimited(), local=True))
    assert i18n.t("ru", "sub.benefit_size_unlimited").replace("{free_size}", "15") in text, text


def test_equal_sizes_are_never_advertised() -> None:
    """Free 15 MB и Pro 15 MB — про размер молчим."""
    pro = Plan("pro", 15, 60, [480], 480, 20, "medium", 0, 0)
    assert "MB" not in pro_benefits("ru", make_ctx(free_15(), pro))


def test_rejection_key_blames_the_plan_or_the_api() -> None:
    cloud = make_ctx(free_15(), pro_unlimited())
    assert size_limit_key(cloud, free_15()) == "conv.too_big"
    assert size_limit_key(cloud, pro_unlimited()) == "conv.too_big_api"
    local = make_ctx(free_15(), pro_unlimited(), local=True)
    assert size_limit_key(local, free_15()) == "conv.too_big"


def test_tariff_summary_shows_unlimited_size_when_local_api() -> None:
    text = tariff_summary("ru", make_ctx(free_15(), pro_unlimited(), local=True))
    assert "15 MB" in text and i18n.t("ru", "sub.limit_none") in text, text


@pytest.mark.parametrize("lang", i18n.SUPPORTED)
def test_free_screen_shows_the_plan_size_in_every_language(lang: str):
    ctx = make_ctx(free_15(), pro_unlimited())
    assert size_text(lang, ctx, free_15()) == "15 MB"


def test_help_shows_numbers_and_no_vague_wording(monkeypatch):
    from handlers.user import menu

    monkeypatch.setattr(menu, "get_ctx", lambda: make_ctx(free_plan(), pro_plan()))
    monkeypatch.setattr(menu, "is_admin", lambda _uid: False)
    text = menu._help_text("ru", {"user_id": 1})
    assert "640×640" in text and "360×360" in text, text
    assert "выше качество" not in text, "в справке осталась расплывчатая формулировка"


def test_help_keeps_admin_block_last(monkeypatch):
    from handlers.user import menu

    monkeypatch.setattr(menu, "get_ctx", lambda: make_ctx(free_plan(), pro_plan()))
    monkeypatch.setattr(menu, "is_admin", lambda _uid: True)
    text = menu._help_text("ru", {"user_id": 999})
    assert text.endswith(i18n.t("ru", "help.admin")), text[-80:]


class _Msg:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text, **_kwargs):
        self.edits.append(text)


async def _noop(*_args, **_kwargs):
    return None


def _returns(value):
    """Подмена async-функции, возвращающей значение."""
    async def inner(*_args, **_kwargs):
        return value
    return inner


def _subscription_cb() -> SimpleNamespace:
    return SimpleNamespace(
        message=_Msg(), from_user=SimpleNamespace(id=1), data="m:sub", answer=_noop,
    )


def _patch_billing(monkeypatch, *, plan, sub=None):
    from handlers.user import billing

    monkeypatch.setattr(billing, "get_ctx", _ctx_for_billing)
    monkeypatch.setattr(billing, "ensure_user", _returns({"user_id": 1, "language": "ru"}))
    monkeypatch.setattr(billing, "user_plan", _returns((plan, sub)))
    monkeypatch.setattr(billing, "is_lifetime", lambda _v: False)
    # cb_message требует настоящий aiogram.Message — подменяем хелпер
    monkeypatch.setattr(billing, "cb_message", lambda cb: cb.message)
    return billing


def _ctx_for_billing() -> SimpleNamespace:
    return SimpleNamespace(
        plans=Plans(free_plan(), pro_plan(), []),
        config=SimpleNamespace(api=APIConfig(local=False)),
        locks=SimpleNamespace(hold=lambda _uid: _noop()),
    )


async def test_pro_owner_sees_no_upsell(monkeypatch):
    """Действующему Pro не показываем «что даёт Pro»."""
    billing = _patch_billing(monkeypatch, plan=pro_plan(),
                             sub={"expires_at": "2030-01-01"})
    cb = _subscription_cb()
    await billing.cb_subscription(cb)
    assert cb.message.edits, "экран подписки не отрисован"
    assert i18n.t("ru", "sub.pro_benefits_title") not in cb.message.edits[0]
    assert "2030-01-01" in cb.message.edits[0]


async def test_free_subscription_screen_names_the_15_mb_limit(monkeypatch):
    from handlers.user import billing

    monkeypatch.setattr(billing, "get_ctx", lambda: SimpleNamespace(
        plans=Plans(free_15(), pro_unlimited(), []),
        config=SimpleNamespace(api=APIConfig(local=False)),
        locks=SimpleNamespace(hold=lambda _uid: _noop()),
    ))
    monkeypatch.setattr(billing, "ensure_user", _returns({"user_id": 1, "language": "ru"}))
    monkeypatch.setattr(billing, "user_plan", _returns((free_15(), None)))
    monkeypatch.setattr(billing, "cb_message", lambda cb: cb.message)
    cb = _subscription_cb()
    await billing.cb_subscription(cb)
    text = cb.message.edits[0]
    assert "Макс. файл: 15 MB" in text, text
    assert "20 MB" in text, "Pro должен показываться с облачным потолком"


async def test_free_owner_sees_the_upsell(monkeypatch):
    billing = _patch_billing(monkeypatch, plan=free_plan())
    cb = _subscription_cb()
    await billing.cb_subscription(cb)
    text = cb.message.edits[0]
    assert i18n.t("ru", "sub.pro_benefits_title") in text
    assert "640×640" in text and "вместо 360×360" in text, text
