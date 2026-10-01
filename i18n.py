"""
Простая i18n: словари в locales/<lang>.json, функция t(lang, key, **kwargs).

Список языков задаётся LANG_NAMES: добавить язык = добавить здесь название и
положить locales/<код>.json с тем же набором ключей. Формат значений — HTML.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LANG = "ru"

# код → подпись в меню выбора языка. Порядок = порядок кнопок.
LANG_NAMES: dict[str, str] = {
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English",
    "uk": "🇺🇦 Українська",
    "kk": "🇰🇿 Қазақша",
    "uz": "🇺🇿 Oʻzbekcha",
    "es": "🇪🇸 Español",
    "de": "🇩🇪 Deutsch",
    "fr": "🇫🇷 Français",
    "pt": "🇧🇷 Português",
    "tr": "🇹🇷 Türkçe",
}
SUPPORTED = tuple(LANG_NAMES)

_locales: dict[str, dict] = {}


def load_locales(locales_dir: str | Path) -> None:
    """
    Загружает все locales/*.json в память.

    Файл для каждого поддерживаемого языка обязателен: без него t() вернёт
    пользователю сам ключ («menu.title»), а не текст, — такую деградацию
    лучше поймать на старте.
    """
    path = Path(locales_dir)
    for lang in SUPPORTED:
        file = path / f"{lang}.json"
        if not file.is_file():
            raise FileNotFoundError(f"Не найден файл локали: {file}")
        _locales[lang] = json.loads(file.read_text(encoding="utf-8"))
        logger.info("Локаль загружена: %s (%d ключей)", lang, len(_locales[lang]))


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
    return LANG_NAMES.get(lang, lang)


def detect_language(language_code: str | None, fallback: str = DEFAULT_LANG) -> str:
    """
    Язык пользователя по language_code из клиента Telegram.

    Telegram присылает региональные варианты ('pt-BR', 'uz-Latn', 'en_US'),
    поэтому сравниваем и код целиком, и его базовую часть. Незнакомый язык —
    fallback (default_language из конфига).
    """
    if not language_code:
        return fallback
    code = language_code.strip().lower().replace("_", "-")
    if code in SUPPORTED:
        return code
    base = code.split("-", 1)[0]
    return base if base in SUPPORTED else fallback
