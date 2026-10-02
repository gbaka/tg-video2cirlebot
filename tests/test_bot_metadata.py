"""Описание бота и «о боте»: локали, лимиты Bot API и публикация.

Эти два поля Telegram показывает ДО первого сообщения, разметку в них не
поддерживает (теги видны как текст), а длинный текст молча обрезает — поэтому
проверяем и содержимое локалей, и саму публикацию.
"""

import json
import re
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramAPIError

import bot_metadata
import i18n

ROOT = Path(__file__).resolve().parent.parent
LANGS = i18n.SUPPORTED
# «О боте» и описание — plain text: HTML/markdown в них Telegram показывает
# как обычные символы. menu.title сюда НЕ входит: это сообщение бота, где теги
# допустимы.
PLAIN_TEXT_KEYS = ("meta.description", "meta.about")


def _locale(lang: str) -> dict:
    return json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", LANGS)
def test_description_and_about_fit_the_api_limits(lang: str) -> None:
    data = _locale(lang)
    assert len(data["meta.description"]) <= bot_metadata.DESCRIPTION_LIMIT, lang
    assert len(data["meta.about"]) <= bot_metadata.SHORT_DESCRIPTION_LIMIT, lang
    assert data["meta.about"].strip() and data["meta.description"].strip()


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", PLAIN_TEXT_KEYS)
def test_fields_are_plain_text(lang: str, key: str) -> None:
    """Ни HTML, ни markdown: Telegram покажет разметку как обычные символы."""
    value = _locale(lang)[key]
    assert "<" not in value and ">" not in value, f"{lang}/{key}: угловые скобки"
    for mark in ("**", "__", "##", "`"):
        assert mark not in value, f"{lang}/{key}: markdown {mark}"


@pytest.mark.parametrize("lang", LANGS)
def test_description_does_not_advertise_limits(lang: str) -> None:
    """Текст привлекает пользователя, а не перечисляет ограничения тарифа.

    Ловим именно упоминания лимитов: «15 MB», «1 минута», «5 в день», «только в
    личных чатах». Плюсы Pro («качество до 640×640», «файлы любого размера»,
    «без ежедневных пауз») разрешены — это выгода, а не ограничение.
    """
    value = _locale(lang)["meta.description"]
    patterns = (
        r"\d+\s?(mb|мб)",                 # размер файла
        r"\d+\s?(min|мин|минут|minute)",  # длительность
        r"\d+\s?(per day|в день|a day)",
        r"(private chats|личных чатах|особистих чатах|жеке чаттарда)",
    )
    hits = [p for p in patterns if re.search(p, value, flags=re.IGNORECASE)]
    assert not hits, f"{lang}: в описании упомянуты ограничения: {hits}"


@pytest.mark.parametrize("lang", LANGS)
def test_description_lists_the_accepted_formats(lang: str) -> None:
    """Приветствие объясняет, что прислать: форматы перечислены одинаково везде."""
    value = _locale(lang)["meta.description"]
    assert "mp4" in value, f"{lang}: в описании не сказано, какие файлы принимаются"


class FakeBot:
    """Запоминает вызовы set_my_description / set_my_short_description."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.descriptions: list[tuple[str | None, str]] = []
        self.short: list[tuple[str | None, str]] = []
        self.fail_for = fail_for or set()

    async def set_my_description(self, description: str, language_code=None) -> bool:
        if language_code in self.fail_for:
            raise TelegramAPIError(method="setMyDescription", message="Bad Request")
        self.descriptions.append((language_code, description))
        return True

    async def set_my_short_description(self, short_description: str, language_code=None) -> bool:
        self.short.append((language_code, short_description))
        return True


@pytest.fixture(autouse=True, scope="module")
def _locales():
    i18n.load_locales(ROOT / "locales")


async def test_every_language_is_published_plus_a_fallback() -> None:
    bot = FakeBot()
    assert await bot_metadata.publish_bot_metadata(bot) == []
    published = [code for code, _ in bot.descriptions]
    assert published == [None, *LANGS], published
    assert [code for code, _ in bot.short] == [None, *LANGS]
    # вариант без language_code собирается на языке по умолчанию
    assert bot.descriptions[0][1] == i18n.t(i18n.DEFAULT_LANG, "meta.description")
    assert bot.descriptions[1][1] == i18n.t(LANGS[0], "meta.description")


async def test_failures_are_reported_not_raised() -> None:
    bot = FakeBot(fail_for={"de"})
    failed = await bot_metadata.publish_bot_metadata(bot)
    assert len(failed) == 1 and failed[0].startswith("de:"), failed
    assert "de" not in [code for code, _ in bot.descriptions], "остальные языки не опубликованы"


async def test_missing_locale_text_is_a_reported_failure(monkeypatch) -> None:
    """Ключа нет — публикуем остальные языки, а не отправляем имя ключа текстом."""
    monkeypatch.setattr(bot_metadata, "_text", lambda lang, key: None)
    bot = FakeBot()
    failed = await bot_metadata.publish_bot_metadata(bot)
    assert bot.descriptions == [] and bot.short == []
    assert len(failed) == len(bot_metadata.LANGS)


def test_long_text_is_cut_at_a_word_boundary(caplog) -> None:
    """Половину слова в описание не отправляем: обрезаем по последнему пробелу."""
    text = "первое второе третье"
    assert bot_metadata._fit(text, 100, "test") == text
    shortened = bot_metadata._fit(text, 13, "test")
    assert shortened == "первое", shortened
    assert "первое второе третье".startswith(shortened)
    assert "длиннее 13" in caplog.text

    wider = bot_metadata._fit(text, 19, "test")
    assert wider == "первое второе", wider


async def test_syncmenu_republishes_commands_and_description(monkeypatch) -> None:
    """Админ-команда обновляет и меню «/», и описание — иначе они расходятся."""
    from types import SimpleNamespace

    from handlers.admin import menu as admin_menu

    published: list[str] = []

    async def hints(bot, admin_ids, fallback=None):
        published.append("hints")
        return []

    async def metadata(bot, fallback=None):
        published.append("metadata")
        return []

    answers: list[str] = []

    class Msg:
        async def answer(self, text, **_kwargs):
            answers.append(text)

    monkeypatch.setattr(admin_menu, "publish_command_hints", hints)
    monkeypatch.setattr(admin_menu, "publish_bot_metadata", metadata)
    monkeypatch.setattr(admin_menu, "admin_guard", _true())
    monkeypatch.setattr(admin_menu, "ensure_user", _returns({"user_id": 1, "language": "ru"}))
    monkeypatch.setattr(admin_menu, "get_ctx", lambda: SimpleNamespace(
        bot=object(), config=SimpleNamespace(bot=SimpleNamespace(admin_ids=[1]),
                                             default_language="ru"),
    ))

    await admin_menu.cmd_syncmenu(Msg())
    assert published == ["hints", "metadata"], published
    assert answers and answers[0] == i18n.t("ru", "admin.syncmenu_ok")


async def test_syncmenu_reports_partial_failure(monkeypatch) -> None:
    from types import SimpleNamespace

    from handlers.admin import menu as admin_menu

    async def hints(bot, admin_ids, fallback=None):
        return []

    async def metadata(bot, fallback=None):
        return ["de: Bad Request"]

    answers: list[str] = []

    class Msg:
        async def answer(self, text, **_kwargs):
            answers.append(text)

    monkeypatch.setattr(admin_menu, "publish_command_hints", hints)
    monkeypatch.setattr(admin_menu, "publish_bot_metadata", metadata)
    monkeypatch.setattr(admin_menu, "admin_guard", _true())
    monkeypatch.setattr(admin_menu, "ensure_user", _returns({"user_id": 1, "language": "ru"}))
    monkeypatch.setattr(admin_menu, "get_ctx", lambda: SimpleNamespace(
        bot=object(), config=SimpleNamespace(bot=SimpleNamespace(admin_ids=[1]),
                                             default_language="ru"),
    ))

    await admin_menu.cmd_syncmenu(Msg())
    assert answers and "de: Bad Request" in answers[0]


def _true():
    async def inner(*_args, **_kwargs):
        return True
    return inner


def _returns(value):
    async def inner(*_args, **_kwargs):
        return value
    return inner
