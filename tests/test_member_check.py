"""Проверка подписки по кнопке: ответ пользователю всегда с текстом, а не только эмодзи."""

import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers.user import billing
from i18n import load_locales, t
from tests.fakes import FakeContext

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"
HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


@pytest.fixture(autouse=True, scope="module")
def _locales():
    load_locales(LOCALES)


class FakeCallback:
    """Кнопка: помним текст и режим ответа."""

    def __init__(self, user_id: int = 42, data: str = "c:check") -> None:
        self.from_user = SimpleNamespace(id=user_id, full_name="Tester", username="tester")
        self.message = SimpleNamespace(
            message_id=1, chat=SimpleNamespace(id=user_id), text="", caption=None
        )
        self.data = data
        self.answers: list[tuple[str, bool]] = []
        self.edits: list[str] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text or "", show_alert))

    async def edit_text(self, text: str, **_kwargs) -> None:
        self.edits.append(text)


@pytest.fixture
def cb_ctx(monkeypatch):
    ctx = FakeContext()
    monkeypatch.setattr(billing, "get_ctx", lambda: ctx)
    monkeypatch.setattr(billing, "ensure_user", AsyncMock(return_value={
        "user_id": 42, "language": "ru",
    }))
    monkeypatch.setattr(billing, "is_admin", lambda _uid: False)
    monkeypatch.setattr(billing, "show_main", AsyncMock())
    return ctx


async def test_non_member_gets_a_readable_alert(cb_ctx, monkeypatch):
    membership = AsyncMock(return_value=False)
    monkeypatch.setattr(billing, "membership_ok", membership)
    cb = FakeCallback()
    await billing.cb_check_membership(cb)
    assert len(cb.answers) == 1
    text, alert = cb.answers[0]
    assert alert is True, "сообщение должно показываться поверх экрана"
    assert text == t("ru", "member.check_failed")
    assert HAS_LETTER.search(text), "ответ состоит только из эмодзи"
    assert "Вступить" in text, "нет указания, что делать дальше"
    assert not cb.edits, "без доступа меню перерисовывать не нужно"


async def test_member_gets_a_confirmation_and_the_menu(cb_ctx, monkeypatch):
    monkeypatch.setattr(billing, "membership_ok", AsyncMock(return_value=True))
    cb = FakeCallback()
    await billing.cb_check_membership(cb)
    text, alert = cb.answers[0]
    assert text == t("ru", "member.check_ok") and HAS_LETTER.search(text)
    assert alert is False
    billing.show_main.assert_awaited_once()


async def test_admin_passes_without_membership(cb_ctx, monkeypatch):
    membership = AsyncMock(return_value=False)
    monkeypatch.setattr(billing, "is_admin", lambda _uid: True)
    monkeypatch.setattr(billing, "membership_ok", membership)
    cb = FakeCallback()
    await billing.cb_check_membership(cb)
    assert cb.answers[0][0] == t("ru", "member.check_ok")
    billing.show_main.assert_awaited_once()
    membership.assert_not_awaited()


async def test_unknown_price_code_is_explained(monkeypatch):
    ctx = SimpleNamespace(plans=SimpleNamespace(price=lambda _code: None))
    monkeypatch.setattr(billing, "get_ctx", lambda: ctx)
    monkeypatch.setattr(billing, "ensure_user", AsyncMock(return_value={
        "user_id": 42, "language": "ru",
    }))
    monkeypatch.setattr(billing, "cb_data", lambda _cb: "b:gone")
    cb = FakeCallback(data="b:gone")
    await billing.cb_buy(cb)
    text, alert = cb.answers[0]
    assert alert is True and text == t("ru", "sub.price_gone")
    assert HAS_LETTER.search(text)


def test_check_messages_exist_in_every_language():
    for lang in ("ru", "en"):
        for key in ("member.check_ok", "member.check_failed"):
            text = t(lang, key)
            assert text != key and HAS_LETTER.search(text)
    assert t("ru", "member.check_failed") != t("en", "member.check_failed")


def test_no_handler_answers_a_callback_with_bare_emoji():
    """Кнопка не должна отвечать только эмодзи — пользователь не поймёт реакцию."""
    offenders = []
    for path in (ROOT / "handlers").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "answer":
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            if not HAS_LETTER.search(first.value):
                offenders.append(f"{path.name}:{node.lineno}: {first.value!r}")
    assert offenders == [], f"ответы из одних эмодзи: {offenders}"
