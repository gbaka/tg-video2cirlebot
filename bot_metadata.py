"""Программная публикация описания бота (экран до /start) и «о боте» (профиль).

Оба поля живут на серверах Telegram и задаются методами setMyDescription /
setMyShortDescription с необязательным language_code. BotFather пишет в то же
хранилище, поэтому владелец должен быть один: здесь это локали — ключи
`meta.description` и `meta.about`.

Текст этих полей — **plain text**: Telegram разметку в них не поддерживает, и
пользователь увидит теги как есть. Поэтому значения локалей обходятся без HTML и
markdown, а `tests/test_bot_metadata.py` следит за этим и за лимитами Bot API.

Публикуется по варианту на каждый язык из `SUPPORTED` плюс вариант без
`language_code` — запасной для клиентов, чей язык бот не знает. Ошибка публикации
не должна ронять запуск бота: это витрина, а не рабочий путь.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from i18n import DEFAULT_LANG, SUPPORTED, t

logger = logging.getLogger(__name__)

#: Лимиты Bot API: более длинный текст Telegram молча обрежет.
DESCRIPTION_LIMIT = 512
SHORT_DESCRIPTION_LIMIT = 120

#: Значения берутся из локалей; ключ нужен как литерал ещё и для теста
#: «нет мёртвых ключей» — он ищет ключи в исходниках.
DESCRIPTION_KEY = "meta.description"
SHORT_DESCRIPTION_KEY = "meta.about"

#: Языки публикации плюс запасной вариант без language_code.
LANGS: tuple[str | None, ...] = (None, *SUPPORTED)


def _fit(text: str, limit: int, label: str) -> str:
    """Обрезает по границе слова, чтобы не отправить половину слова.

    Лимит в Telegram жёсткий, а тексты приходят из локалей: лучше потерять
    последнюю фразу и сказать об этом в лог, чем получить отказ API.
    """
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:—–-")
    logger.warning("%s: текст длиннее %d символов, обрезан до %d", label, limit, len(shortened))
    return shortened


def _text(lang: str, key: str) -> str | None:
    """Текст локали или None, если перевод отсутствует."""
    value = t(lang, key)
    if not value or value == key:
        logger.warning("Нет перевода %s для языка %s", key, lang)
        return None
    return value


async def publish_bot_metadata(bot: Bot, fallback: str = DEFAULT_LANG) -> list[str]:
    """Пишет описание и «о боте» для каждого языка; возвращает список неудач."""
    failed: list[str] = []
    for lang in LANGS:
        language = lang or fallback
        label = lang or "default"
        description = _text(language, DESCRIPTION_KEY)
        about = _text(language, SHORT_DESCRIPTION_KEY)
        if description is None or about is None:
            failed.append(f"{label}: нет текста в локали")
            continue
        try:
            await bot.set_my_description(
                description=_fit(description, DESCRIPTION_LIMIT, f"description/{label}"),
                language_code=lang,
            )
            await bot.set_my_short_description(
                short_description=_fit(about, SHORT_DESCRIPTION_LIMIT, f"about/{label}"),
                language_code=lang,
            )
        except TelegramAPIError as e:
            failed.append(f"{label}: {e}")
            logger.warning("Описание не опубликовано (%s): %s", label, e)
    if not failed:
        logger.info("Описание и about опубликованы (вариантов: %d)", len(LANGS))
    return failed
