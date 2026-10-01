"""Локали: паритет языков, рендер всех шаблонов, отсутствие мёртвых/битых ключей."""

import re
import string
from pathlib import Path

import pytest

import i18n

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED = i18n.SUPPORTED


def _locales() -> dict[str, dict]:
    import json

    return {
        lang: json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        for lang in SUPPORTED
    }


def test_languages_have_same_keys() -> None:
    """Набор ключей обязан совпадать у всех языков — иначе дыра в переводе."""
    data = _locales()
    base = set(data[i18n.DEFAULT_LANG])
    for lang, values in data.items():
        only_here = sorted(set(values) - base)
        only_base = sorted(base - set(values))
        assert not only_here and not only_base, (
            f"{lang}: лишние {only_here}, отсутствуют {only_base}"
        )


@pytest.mark.parametrize("lang", SUPPORTED)
def test_every_language_is_named_and_selectable(lang: str) -> None:
    """Язык без подписи не попадёт в меню выбора с понятной кнопкой."""
    assert i18n.lang_name(lang) != lang, f"нет названия для языка {lang}"


@pytest.mark.parametrize("lang", SUPPORTED)
def test_no_duplicate_texts_across_keys(lang: str) -> None:
    """Дубли текстов означают, что один ключ лишний."""
    data = _locales()[lang]
    seen: dict[str, list[str]] = {}
    for key, value in data.items():
        seen.setdefault(value, []).append(key)
    duplicates = {v: k for v, k in seen.items() if len(k) > 1}
    assert not duplicates, f"{lang}: дублирующиеся тексты: {duplicates}"


@pytest.mark.parametrize("lang", SUPPORTED)
def test_every_template_renders(lang: str) -> None:
    """Подставляем фиктивные значения во все плейсхолдеры каждого шаблона."""
    for key, template in _locales()[lang].items():
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
        rendered = i18n.t(lang, key, **{f: f"<{f}>" for f in fields})
        assert rendered, f"пустой текст: {lang}/{key}"
        assert "{" not in rendered.replace("{{", ""), f"незаменённый плейсхолдер: {lang}/{key}"


def _source_text() -> str:
    files = [p for p in ROOT.rglob("*.py") if "tests" not in p.parts]
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def test_keys_used_in_code_exist() -> None:
    """Ключи из вызовов t(<lang>, "ключ") должны быть в локалях."""
    data = _locales()["ru"]
    used = set(re.findall(r'\bt\([^,]+,\s*"([a-z][a-z0-9_.]+)"', _source_text()))
    missing = sorted(k for k in used if k not in data)
    assert not missing, f"ключи без перевода: {missing}"


def test_no_dead_keys() -> None:
    """Каждый ключ локали должен встречаться в коде как строковый литерал."""
    from command_hints import ADMIN_COMMANDS

    source = _source_text()
    literals = set(re.findall(r'"([a-z][a-z0-9_.]+)"', source))
    # Описания команд собираются как f"cmd.{имя}" из списков, а не литералами.
    literals |= {f"cmd.{name}" for name in ADMIN_COMMANDS}
    dead = sorted(set(_locales()["ru"]) - literals)
    assert not dead, f"неиспользуемые ключи: {dead}"


def test_unknown_language_falls_back_to_default() -> None:
    assert i18n.t("xx", "btn.back") == i18n.t("ru", "btn.back")


def test_missing_key_returns_itself() -> None:
    assert i18n.t("ru", "no.such.key") == "no.such.key"


def test_broken_placeholder_does_not_raise() -> None:
    """Лишний placeholder не должен ронять бота — возвращаем шаблон как есть."""
    assert i18n.t("ru", "users.showing", wrong=1)


def test_lang_name_known_codes() -> None:
    assert "Русский" in i18n.lang_name("ru")
    assert "English" in i18n.lang_name("en")
    assert i18n.lang_name("xx") == "xx"


def test_language_menu_offers_every_supported_language() -> None:
    """Меню выбора языка строится из SUPPORTED, а не из списка в menus.py."""
    import menus

    for current in SUPPORTED:
        rows = menus.language_menu("ru", current).inline_keyboard
        codes = [b.callback_data for row in rows for b in row
                 if b.callback_data.startswith("l:")]
        assert codes == [f"l:{lang}" for lang in SUPPORTED], codes
        marked = [b.text for row in rows for b in row if b.text.startswith("✅")]
        assert len(marked) == 1 and i18n.lang_name(current) in marked[0]
