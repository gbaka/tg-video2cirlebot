"""Язык нового пользователя определяется по клиенту Telegram."""

from types import SimpleNamespace

import pytest

import access
import i18n
from i18n import detect_language
from plans import Plan
from repositories import UserRepo


@pytest.mark.parametrize("code,expected", [
    ("ru", "ru"),
    ("en", "en"),
    ("EN", "en"),
    ("en-US", "en"),
    ("de-AT", "de"),
    ("pt-br", "pt"),
    ("fr_FR", "fr"),
    ("uz-Latn", "uz"),
    ("kk-Cyrl-KZ", "kk"),
    ("tr", "tr"),
    ("uk", "uk"),
])
def test_telegram_codes_map_to_shipped_languages(code: str, expected: str) -> None:
    assert detect_language(code) == expected


@pytest.mark.parametrize("code", [None, "", "   ", "zh-hans", "ja", "xx-YY"])
def test_unknown_or_missing_language_falls_back(code) -> None:
    assert detect_language(code, "en") == "en"


def test_fallback_defaults_to_the_default_language() -> None:
    assert detect_language(None) == i18n.DEFAULT_LANG


def test_detection_never_returns_a_language_without_locale() -> None:
    """detect_language обязан возвращать только язык из SUPPORTED."""
    for code in ("ru", "en-US", "pt-BR", "zh", "ja", None, "", "zz"):
        assert detect_language(code) in i18n.SUPPORTED


async def test_new_user_gets_the_client_language(monkeypatch, db):
    """ensure_user передаёт в репозиторий язык клиента, а не конфиг."""
    captured = {}

    class FakeUsers:
        async def get_or_create(self, **kwargs):
            captured.update(kwargs)
            return {"user_id": kwargs["user_id"], "language": kwargs["default_language"]}

    ctx = SimpleNamespace(
        users=FakeUsers(),
        config=SimpleNamespace(default_language="ru"),
    )
    monkeypatch.setattr(access, "get_ctx", lambda: ctx)
    event = SimpleNamespace(from_user=SimpleNamespace(
        id=5, username="u", full_name="User Name", first_name="User", language_code="es-MX",
    ))

    user = await access.ensure_user(event)
    assert user["language"] == "es"
    assert captured["user_id"] == 5 and captured["first_name"] == "User Name"


async def test_existing_language_is_not_overwritten(db):
    """Пользователь сменил язык вручную — старт с другого клиента его не сбросит."""
    users = UserRepo(db)
    await users.get_or_create(user_id=7, username="u", first_name="Name",
                              default_language="ru")
    await users.set_language(7, "en")
    row = await users.get_or_create(
        user_id=7, username="u", first_name="Name", default_language="es",
    )
    assert row["language"] == "en"


async def test_switch_language_accepts_only_supported(monkeypatch):
    """Кнопка языка с несуществующим кодом не должна менять настройку."""
    from handlers.user import settings

    calls: list[tuple[int, str]] = []

    class FakeUsers:
        async def set_language(self, user_id, lang):
            calls.append((user_id, lang))

    class Msg:
        async def edit_text(self, *_args, **_kwargs):
            return None

    ctx = SimpleNamespace(users=FakeUsers())
    monkeypatch.setattr(settings, "get_ctx", lambda: ctx)
    monkeypatch.setattr(settings, "ensure_user",
                        _returns({"user_id": 1, "language": "ru", "quality": None}))
    monkeypatch.setattr(settings, "cb_data", lambda _cb: _cb.data)
    monkeypatch.setattr(settings, "cb_message", lambda cb: cb.message)
    monkeypatch.setattr(settings, "user_plan",
                        _returns((Plan("pro", 20, 60, [480], 480, 20, "fast"), None)))
    monkeypatch.setattr(settings, "video_settings_text", lambda *_a, **_k: "")

    cb = SimpleNamespace(
        data="l:pt", from_user=SimpleNamespace(id=1),
        answer=_returns(None), message=Msg(),
    )
    await settings.cb_set_lang(cb)
    assert calls == [(1, "pt")], calls

    calls.clear()
    cb.data = "l:zh"
    await settings.cb_set_lang(cb)
    assert calls == [], "несуществующий язык не должен сохраняться"


def _returns(value):
    async def inner(*_args, **_kwargs):
        return value
    return inner
