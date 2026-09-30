"""Клавиатуры: все меню строятся, callback_data корректны."""

import pytest

import menus
from plans import Plan, Plans, PriceOption

LANGS = ("ru", "en")


def _plans() -> Plans:
    free = Plan("free", 50, 60, [360], 360, 24, "fast", 5, 3)
    pro = Plan("pro", 200, 60, [360, 480, 640], 480, 20, "medium", 0, 0)
    return Plans(free, pro, [
        PriceOption("pro_30d", "pro", 30, 25),
        PriceOption("pro_life", "pro", 0, 150, lifetime=True),
    ])


def _callbacks(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row]


@pytest.mark.parametrize("lang", LANGS)
def test_main_and_admin_menus(lang: str) -> None:
    assert menus.main_menu(lang, True).inline_keyboard
    assert menus.main_menu(lang, False).inline_keyboard
    admin_cbs = _callbacks(menus.admin_menu(lang))
    assert "a:stats" in admin_cbs and "m:main" in admin_cbs   # назад в главное меню
    assert menus.settings_menu(lang, True).inline_keyboard
    assert menus.settings_menu(lang, False).inline_keyboard
    assert menus.language_menu(lang, lang).inline_keyboard
    assert menus.channel_menu(lang).inline_keyboard
    assert menus.bot_settings_menu(lang, True, False).inline_keyboard
    assert menus.membership_kb(lang, "https://t.me/x").inline_keyboard
    assert menus.back_main(lang).inline_keyboard


@pytest.mark.parametrize("lang", LANGS)
def test_quality_menu_marks_current(lang: str) -> None:
    kb = menus.quality_menu(lang, [360, 480], 480)
    marked = [b.text for row in kb.inline_keyboard for b in row if b.text.startswith("✅")]
    assert len(marked) == 1


@pytest.mark.parametrize("lang", LANGS)
def test_subscription_menu_without_and_with_cancel(lang: str) -> None:
    plans = _plans()
    plain = _callbacks(menus.subscription_menu(lang, plans.prices))
    assert "b:pro_30d" in plain and "b:pro_life" in plain
    assert "s:off" not in plain                       # Free не может отменить

    pro = _callbacks(menus.subscription_menu(lang, plans.prices, is_pro=True))
    assert "s:off" in pro
    assert "s:off:yes" in _callbacks(menus.sub_cancel_confirm(lang))


@pytest.mark.parametrize("lang", LANGS)
def test_users_pagination_and_cards(lang: str) -> None:
    rows = [
        {"user_id": 1, "first_name": "Bob", "username": "bob", "active_plan": "pro"},
        {"user_id": 2, "first_name": None, "username": None, "active_plan": None},
    ]
    cbs = _callbacks(menus.users_page_menu(lang, 10, 10, 26, "", rows))
    assert "u:v:1" in cbs and "u:v:2" in cbs
    assert "u:p:0" in cbs and "u:p:20" in cbs         # назад и вперёд
    assert "a:export" in cbs and "u:srch" in cbs

    first = _callbacks(menus.users_page_menu(lang, 0, 10, 26, "", rows))
    assert "u:p:0" not in first                       # на первой странице назад некуда
    assert "u:clr" in _callbacks(menus.users_page_menu(lang, 0, 10, 26, "bob", rows))
    assert menus.users_search_cancel(lang).inline_keyboard


@pytest.mark.parametrize("lang", LANGS)
def test_user_card_menu_shows_revoke_only_for_pro(lang: str) -> None:
    assert "u:rv:5" in _callbacks(menus.user_card_menu(lang, 5, True))
    assert "u:rv:5" not in _callbacks(menus.user_card_menu(lang, 5, False))
    assert "u:gf:5" in _callbacks(menus.user_card_menu(lang, 5, False))


def test_callback_data_fits_telegram_limit() -> None:
    """Telegram разрешает не больше 64 байт в callback_data."""
    big_id = 9_999_999_999
    keyboards = [
        menus.user_card_menu("ru", big_id, True),
        menus.users_page_menu("ru", 9990, 10, 20000, "", [{"user_id": big_id, "first_name": "x"}]),
        menus.admin_menu("ru"),
        menus.subscription_menu("ru", _plans().prices, is_pro=True),
        menus.sub_cancel_confirm("ru"),
        menus.bot_settings_menu("ru", True, True),
    ]
    for kb in keyboards:
        for cb in _callbacks(kb):
            assert cb and len(cb.encode()) <= 64, cb
