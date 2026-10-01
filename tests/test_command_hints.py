"""Подсказки команд: публикация по scope и синхронность с зарегистрированными командами."""

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

import command_hints
import i18n
from command_hints import ADMIN_COMMANDS, USER_COMMANDS, build, publish_command_hints
from i18n import load_locales, t

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"
COMMAND_NAME = re.compile(r"^[a-z0-9_]{1,32}$")


@pytest.fixture(autouse=True, scope="module")
def _locales():
    load_locales(LOCALES)


def registered_commands() -> set[str]:
    """Имена команд, реально зарегистрированные в хендлерах (источник правды)."""
    found = set()
    for path in (ROOT / "handlers").rglob("*.py"):
        found |= set(re.findall(r'Command\(\s*"([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    return found


class FakeBot:
    def __init__(self, fail_for=()):
        self.calls = []
        self.fail_for = set(fail_for)

    async def set_my_commands(self, commands, scope=None, language_code=None):
        scope = scope if scope is not None else BotCommandScopeDefault()
        entry = (type(scope).__name__, getattr(scope, "chat_id", None), language_code, commands)
        self.calls.append(entry)
        key = (getattr(scope, "chat_id", None), language_code)
        if key in self.fail_for:
            raise TelegramBadRequest(method=None, message="Bad Request: chat not found")
        return True


def test_every_registered_command_is_published():
    registered = registered_commands()
    published = set(USER_COMMANDS) | set(ADMIN_COMMANDS)
    assert registered - published == set(), "команда зарегистрирована, но не попала в подсказки"
    assert published - registered == set(), "подсказка есть, а команды в хендлерах нет"


def test_command_lists_match_the_code_order_and_are_valid():
    for name in USER_COMMANDS + ADMIN_COMMANDS:
        assert COMMAND_NAME.match(name), f"недопустимое имя команды: {name}"
    assert len(set(USER_COMMANDS)) == len(USER_COMMANDS)
    assert len(set(ADMIN_COMMANDS)) == len(ADMIN_COMMANDS)
    assert set(USER_COMMANDS) <= set(ADMIN_COMMANDS), "админ видит всё то же, что и пользователь"
    assert len(ADMIN_COMMANDS) <= 100, "Telegram принимает не больше 100 команд на список"


def test_registered_admin_commands_are_in_the_admin_list():
    """Админские хендлеры нельзя показать обычным пользователям."""
    admin_sources = (ROOT / "handlers" / "admin").rglob("*.py")
    admin_only = set()
    for path in admin_sources:
        source = path.read_text(encoding="utf-8")
        admin_only |= set(re.findall(r'Command\(\s*"([a-z0-9_]+)"', source))
    assert admin_only - set(ADMIN_COMMANDS) == set()
    assert admin_only & set(USER_COMMANDS) == set(), "админская команда попала в общий список"


def test_descriptions_exist_in_every_language_and_fit_the_limit():
    for name in ADMIN_COMMANDS:
        for lang in i18n.SUPPORTED:
            text = t(lang, f"cmd.{name}")
            assert text != f"cmd.{name}", f"нет описания cmd.{name} ({lang})"
            assert 1 <= len(text) <= 256, f"описание cmd.{name} ({lang}) вне лимита"


def test_build_uses_the_requested_language():
    ru = build(USER_COMMANDS, "ru")
    en = build(USER_COMMANDS, "en")
    assert [c.command for c in ru] == list(USER_COMMANDS)
    assert ru[0].description != en[0].description
    assert build(ADMIN_COMMANDS, None)[0].description == t("ru", "cmd.start")


def test_every_admin_command_handler_guards_on_the_first_line():
    """Админские команды должны проверять права первым же оператором.

    Проверка по AST, а не по тексту: строковый поиск «admin_guard» проходил бы
    и на `if False and not await admin_guard(...)`, то есть на мёртвом коде.
    """
    unguarded = {}
    for path in (ROOT / "handlers" / "admin").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            commands = [
                re.search(r"Command\(\s*['\"]([a-z0-9_]+)['\"]", ast.unparse(dec))
                for dec in node.decorator_list
            ]
            names = [m.group(1) for m in commands if m]
            if not names:
                continue
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]  # докстрока
            first = body[0] if body else None
            guarded = (
                isinstance(first, ast.If)
                and isinstance(first.test, ast.UnaryOp)
                and any(
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "admin_guard"
                    for sub in ast.walk(first.test)
                )
            )
            if not guarded:
                unguarded[names[0]] = f"{path.name}:{node.lineno}"
    assert unguarded == {}, f"команды без проверки прав первым оператором: {unguarded}"


async def test_publish_covers_base_scopes_and_each_admin_chat():
    bot = FakeBot()
    failed = await publish_command_hints(bot, [111, 222])
    assert failed == []
    base = [c for c in bot.calls if c[0] in ("BotCommandScopeDefault",
                                             "BotCommandScopeAllPrivateChats")]
    assert len(base) == 2 * len(command_hints.LANGS), "базовый список без языковых вариантов"
    admins = [c for c in bot.calls if c[0] == "BotCommandScopeChat"]
    assert {c[1] for c in admins} == {111, 222}
    assert len(admins) == 2 * len(command_hints.LANGS)
    for _scope, _chat, lang, commands in admins:
        names = [c.command for c in commands]
        assert names == list(ADMIN_COMMANDS), (lang, names)
    for _scope, _chat, _lang, commands in base:
        assert [c.command for c in commands] == list(USER_COMMANDS)


async def test_language_variants_and_plain_fallback_are_published():
    bot = FakeBot()
    await publish_command_hints(bot, [111])
    langs = {c[2] for c in bot.calls if c[0] == "BotCommandScopeDefault"}
    assert langs == set(command_hints.LANGS), "нет запасного списка без language_code"
    # каждая локаль должна получить свой список, а не только ru/en
    assert set(i18n.SUPPORTED) <= langs, langs
    for code in i18n.SUPPORTED:
        assert code in langs, f"нет подсказок для языка {code}"


async def test_every_language_gets_its_own_descriptions():
    """Описания команд не должны оставаться русскими для остальных языков."""
    bot = FakeBot()
    await publish_command_hints(bot, [])
    described = {c[2]: c[3][0].description for c in bot.calls
                 if c[0] == "BotCommandScopeDefault"}
    assert described[None] == t("ru", "cmd.start"), "запасной список не на ru"
    for code in i18n.SUPPORTED:
        assert described[code] == t(code, "cmd.start"), code
    assert described["kk"] != described[None], "казахский повторяет запасной список"


async def test_fallback_list_follows_the_configured_default_language():
    bot = FakeBot()
    await publish_command_hints(bot, [], fallback="de")
    described = {c[2]: c[3][0].description for c in bot.calls
                 if c[0] == "BotCommandScopeDefault"}
    assert described[None] == t("de", "cmd.start")
    assert described["de"] == t("de", "cmd.start")


async def test_admin_chat_failure_does_not_stop_other_admins():
    bot = FakeBot(fail_for={(111, None), (111, "ru"), (111, "en")})
    failed = await publish_command_hints(bot, [111, 222])
    assert len(failed) == 3 and all("111" in item for item in failed)
    assert {c[1] for c in bot.calls if c[0] == "BotCommandScopeChat"} == {111, 222}
    assert any(c[1] == 222 for c in bot.calls)


async def test_publish_without_admins_only_sets_the_base_list():
    bot = FakeBot()
    assert await publish_command_hints(bot, []) == []
    assert {c[0] for c in bot.calls} == {"BotCommandScopeDefault",
                                         "BotCommandScopeAllPrivateChats"}


async def test_admin_scope_outranks_the_base_scope():
    """Telegram берёт самый конкретный scope: chat важнее all_private_chats."""
    bot = FakeBot()
    await publish_command_hints(bot, [111])
    chat_call = next(c for c in bot.calls if c[0] == "BotCommandScopeChat")
    assert isinstance(chat_call[3][0].description, str)
    assert hasattr(BotCommandScopeChat(chat_id=1), "chat_id")
    assert isinstance(BotCommandScopeAllPrivateChats(), object)


def test_syncmenu_handler_is_admin_only(monkeypatch):
    from handlers.admin import menu

    asked = []

    async def guard(_event):
        asked.append(True)
        return False

    monkeypatch.setattr(menu, "admin_guard", guard)
    monkeypatch.setattr(menu, "get_ctx", lambda: pytest.fail("не должен вызываться"))

    import asyncio

    message = SimpleNamespace(answer=lambda *a, **k: None)
    asyncio.run(menu.cmd_syncmenu(message))
    assert asked == [True]
