"""
Простая i18n: словари в locales/<lang>.json, функция t(lang, key, **kwargs).

Поддерживаемые языки: ru, en. Формат значений — HTML (parse_mode=HTML).
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LANG = "ru"
SUPPORTED = ("ru", "en")

_locales: dict[str, dict] = {}


def load_locales(locales_dir: str | Path) -> None:
    """Загружает все locales/*.json в память."""
    path = Path(locales_dir)
    for lang in SUPPORTED:
        file = path / f"{lang}.json"
        if file.exists():
            _locales[lang] = json.loads(file.read_text(encoding="utf-8"))
            logger.info("Локаль загружена: %s (%d ключей)", lang, len(_locales[lang]))
        else:
            logger.warning("Локаль не найдена: %s", file)


def t(lang: str, key: str, /, **kwargs) -> str:
    """
    Возвращает строку по ключу с подстановкой {placeholders}.
    lang — позиционный параметр, чтобы плейсхолдер {lang} в текстах не конфликтовал.
    Fallback: язык по умолчанию → сам ключ.
    """
    lang = lang if lang in _locales else DEFAULT_LANG
    template = _locales.get(lang, {}).get(key)
    if template is None:
        template = _locales.get(DEFAULT_LANG, {}).get(key, key)
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError, ValueError) as e:
        logger.warning("Ошибка подстановки в ключе '%s': %s", key, e)
        return template


def lang_name(lang: str) -> str:
    return {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}.get(lang, lang)
