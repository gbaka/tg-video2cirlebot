"""
Импорт всех модулей и связность роутеров.

Ловит то, что не видно при проверке отдельных функций: ошибки уровня модуля
(битые литералы, неразрешённые имена), потерянные при разбиении обработчики
и кнопки меню, на которые никто не реагирует.
"""

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import menus
from plans import Plan, Plans, PriceOption

ROOT = Path(__file__).resolve().parent.parent

MODULES = sorted(
    ".".join(p.relative_to(ROOT).with_suffix("").parts)
    for p in ROOT.rglob("*.py")
    if "tests" not in p.parts and p.name != "__init__.py"
)

LANGS = ("ru", "en")


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module: str) -> None:
    importlib.import_module(module)


def _plans() -> Plans:
    return Plans(
        Plan("free", 50, 60, [360], 360, 24, "fast", 5, 3),
        Plan("pro", 200, 60, [360, 480, 640], 480, 20, "medium", 0, 0),
        [
            PriceOption("pro_30d", "pro", 30, 25),
            PriceOption("pro_life", "pro", 0, 150, lifetime=True),
        ],
    )


def _all_keyboards() -> list:
    """Все клавиатуры, которые бот может показать."""
    rows = [
        {"user_id": 1, "first_name": "Bob", "username": "bob", "active_plan": "pro"},
        {"user_id": 2, "first_name": None, "username": None, "active_plan": None},
    ]
    prices = _plans().prices
    kbs = []
    for lang in LANGS:
        kbs += [
            menus.main_menu(lang, True),
            menus.main_menu(lang, False),
            menus.admin_menu(lang),
            menus.settings_menu(lang, True),
            menus.settings_menu(lang, False),
            menus.language_menu(lang, lang),
            menus.quality_menu(lang, [360, 480, 640], 480),
            menus.subscription_menu(lang, prices, is_pro=True),
            menus.subscription_menu(lang, prices, is_pro=False),
            menus.sub_cancel_confirm(lang),
            menus.channel_menu(lang),
            menus.bot_settings_menu(lang, True, True),
            menus.membership_kb(lang, "https://t.me/x"),
            menus.back_main(lang),
            menus.users_page_menu(lang, 10, 10, 26, "bob", rows),
            menus.users_page_menu(lang, 0, 10, 26, "", rows),
            menus.users_search_cancel(lang),
            menus.user_card_menu(lang, 7, True),
            menus.user_card_menu(lang, 7, False),
        ]
    return kbs


def _all_callback_data() -> list[str]:
    data = {
        b.callback_data
        for kb in _all_keyboards()
        for row in kb.inline_keyboard
        for b in row
        if b.callback_data
    }
    return sorted(data)


async def _handler_matches(handler, event) -> bool:
    for flt in handler.filters:
        result = flt.callback(event)
        if hasattr(result, "__await__"):
            result = await result
        if not result:
            return False
    return True


async def _routers_handling(data: str) -> list[str]:
    """Имена роутеров, которые реагируют на такое callback_data."""
    from handlers.admin import router as admin_router
    from handlers.user import router as user_router

    event = SimpleNamespace(data=data)
    found = []
    for top in (admin_router, user_router):
        for sub in [top, *top.sub_routers]:
            for handler in sub.callback_query.handlers:
                if await _handler_matches(handler, event):
                    found.append(sub.name)
                    break
    return found


def test_routers_are_nested_but_handlers_survive() -> None:
    """Разбиение на модули не потеряло обработчики."""
    import main
    from handlers.admin import router as admin_router
    from handlers.user import router as user_router

    dp = main.build_dispatcher()
    assert [r.name for r in dp.sub_routers] == ["admin", "user"]

    for top, expected in (
        (
            admin_router,
            [
                "admin.menu",
                "admin.stats",
                "admin.users",
                "admin.settings",
                "admin.channel",
                "admin.cards",
                "admin.export",
            ],
        ),
        (user_router, ["user.menu", "user.settings", "user.billing", "user.media"]),
    ):
        assert [r.name for r in top.sub_routers] == expected
        for sub in top.sub_routers:
            assert sub.message.handlers or sub.callback_query.handlers, f"{sub.name} пуст"


@pytest.mark.parametrize("data", _all_callback_data())
async def test_every_menu_button_is_handled(data: str) -> None:
    """На каждую кнопку, которую рисует бот, есть обработчик."""
    found = await _routers_handling(data)
    assert found, f"кнопка {data!r} ничем не обрабатывается"


@pytest.mark.parametrize("data", _all_callback_data())
async def test_menu_button_is_handled_exactly_once(data: str) -> None:
    """Дублей нет: иначе обработку перехватывал бы первый по порядку роутер."""
    found = await _routers_handling(data)
    assert len(found) <= 1, f"на {data!r} реагируют сразу: {found}"


async def test_unknown_callback_is_not_handled() -> None:
    assert await _routers_handling("m:такого-нет") == []
