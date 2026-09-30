"""Локали: паритет языков, рендер всех шаблонов, отсутствие мёртвых/битых ключей."""

import re
import string
from pathlib import Path

import pytest

import i18n

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED = ("ru", "en")


def _locales() -> dict[str, dict]:
    import json

    return {
        lang: json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        for lang in SUPPORTED
    }


def test_languages_have_same_keys() -> None:
    data = _locales()
    ru, en = set(data["ru"]), set(data["en"])
    assert ru == en, f"только ru: {sorted(ru - en)}\nтолько en: {sorted(en - ru)}"


def test_no_duplicate_texts_across_keys() -> None:
    """Дубли текстов означают, что один ключ лишний."""
    data = _locales()["ru"]
    seen: dict[str, list[str]] = {}
    for key, value in data.items():
        seen.setdefault(value, []).append(key)
    duplicates = {v: k for v, k in seen.items() if len(k) > 1}
    assert not duplicates, f"дублирующиеся тексты: {duplicates}"


@pytest.mark.parametrize("lang", SUPPORTED)
def test_every_template_renders(lang: str) -> None:
    """Подставляем фиктивные значения во все плейсхолдеры каждого шаблона."""
    for key, template in _locales()[lang].items():
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
        rendered = i18n.t(lang, key, **{f: f"<{f}>" for f in fields})
        assert rendered, f"пустой текст: {lang}/{key}"
        assert "{" not in rendered.replace("{{", ""), f"незаменённый плейсхолдер: {lang}/{key}"


def _source_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in ROOT.glob("*.py"))


def test_keys_used_in_code_exist() -> None:
    """Ключи из вызовов t(<lang>, "ключ") должны быть в локалях."""
    data = _locales()["ru"]
    used = set(re.findall(r'\bt\([^,]+,\s*"([a-z][a-z0-9_.]+)"', _source_text()))
    missing = sorted(k for k in used if k not in data)
    assert not missing, f"ключи без перевода: {missing}"


def test_no_dead_keys() -> None:
    """Каждый ключ локали должен встречаться в коде как строковый литерал."""
    source = _source_text()
    literals = set(re.findall(r'"([a-z][a-z0-9_.]+)"', source))
    dead = sorted(set(_locales()["ru"]) - literals)
    assert not dead, f"неиспользуемые ключи: {dead}"


def test_unknown_language_falls_back_to_default() -> None:
    assert i18n.t("de", "btn.back") == i18n.t("ru", "btn.back")


def test_missing_key_returns_itself() -> None:
    assert i18n.t("ru", "no.such.key") == "no.such.key"


def test_broken_placeholder_does_not_raise() -> None:
    """Лишний placeholder не должен ронять бота — возвращаем шаблон как есть."""
    assert i18n.t("ru", "users.showing", wrong=1)


def test_lang_name_known_codes() -> None:
    assert "Русский" in i18n.lang_name("ru")
    assert "English" in i18n.lang_name("en")
    assert i18n.lang_name("xx") == "xx"
