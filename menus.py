"""
Inline-клавиатуры (меню). Единая точка построения всех клавиатур.

Схема callback_data:
  m:<раздел>     — навигация по меню (main/info/sub/profile/settings/help/admin/lang/quality)
  l:<lang>       — выбор языка
  q:<res>        — выбор качества
  b:<price>      — покупка подписки (price code)
  a:<раздел>     — админ-разделы (stats/users/tasks/bset/chan)
  bs:<key>       — переключение настройки бота (maintenance/membership)
  c:check        — проверить подписку
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from i18n import t
from plans import PriceOption


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def main_menu(lang: str, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [_btn(t(lang, "btn.howto"), "m:info")],
        [
            _btn(t(lang, "btn.subscription"), "m:sub"),
            _btn(t(lang, "btn.profile"), "m:profile"),
        ],
        [
            _btn(t(lang, "btn.settings"), "m:settings"),
            _btn(t(lang, "btn.help"), "m:help"),
        ],
    ]
    if is_admin:
        rows.append([_btn(t(lang, "btn.admin"), "m:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_main(lang: str, is_admin: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t(lang, "btn.back"), "m:main")],
    ])


def admin_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t(lang, "btn.stats"), "a:stats")],
        [_btn(t(lang, "btn.users"), "a:users")],
        [_btn(t(lang, "btn.tasks"), "a:tasks")],
        [_btn(t(lang, "btn.export"), "a:export")],
        [_btn(t(lang, "btn.bot_settings"), "a:bset")],
        [_btn(t(lang, "btn.channel"), "a:chan")],
        [_btn(t(lang, "btn.back"), "m:main")],
    ])


def subscription_menu(
    lang: str, prices: list[PriceOption], is_pro: bool = False
) -> InlineKeyboardMarkup:
    rows = []
    for p in prices:
        label = (t(lang, "sub.buy_btn_life", stars=p.stars) if p.lifetime
                 else t(lang, "sub.buy_btn", days=p.days, stars=p.stars))
        rows.append([_btn(label, f"b:{p.code}")])
    if is_pro:
        rows.append([_btn(t(lang, "sub.cancel_btn"), "s:off")])
    rows.append([_btn(t(lang, "btn.back"), "m:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sub_cancel_confirm(lang: str) -> InlineKeyboardMarkup:
    """Подтверждение досрочной отмены подписки."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t(lang, "sub.cancel_yes"), "s:off:yes")],
        [_btn(t(lang, "sub.cancel_no"), "m:sub")],
    ])


def user_card_menu(lang: str, user_id: int, is_pro: bool) -> InlineKeyboardMarkup:
    """Действия с конкретным пользователем."""
    rows = [[_btn(t(lang, "card.btn_gift"), f"u:gf:{user_id}")]]
    if is_pro:
        rows.append([_btn(t(lang, "card.btn_revoke"), f"u:rv:{user_id}")])
    rows.append([_btn(t(lang, "btn.back"), "a:users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu(lang: str, is_pro: bool) -> InlineKeyboardMarkup:
    rows = [
        [_btn(t(lang, "btn.language"), "m:lang")],
        [_btn(t(lang, "video.framing_btn"), "m:framing")],
        [_btn(t(lang, "video.fragment_btn"), "m:fragment")],
    ]
    if is_pro:
        rows.append([_btn(t(lang, "btn.quality"), "m:quality")])
    rows.append([_btn(t(lang, "btn.back"), "m:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def framing_menu(lang: str, current: str) -> InlineKeyboardMarkup:
    rows = []
    for mode, key in (("crop", "video.mode_crop"), ("fit", "video.mode_fit")):
        label = t(lang, key)
        if mode == current:
            label = f"✅ {label}"
        rows.append([_btn(label, f"v:mode:{mode}")])
    rows.append([_btn(t(lang, "btn.back"), "m:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fragment_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t(lang, "video.fragment_reset_btn"), "v:fragment:reset")],
        [_btn(t(lang, "btn.back"), "m:settings")],
    ])


def language_menu(lang: str, current: str) -> InlineKeyboardMarkup:
    def mark(code: str, label: str) -> str:
        return f"✅ {label}" if code == current else label

    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(mark("ru", "🇷🇺 Русский"), "l:ru")],
        [_btn(mark("en", "🇬🇧 English"), "l:en")],
        [_btn(t(lang, "btn.back"), "m:settings")],
    ])


def quality_menu(lang: str, resolutions: list[int], current: int) -> InlineKeyboardMarkup:
    rows = []
    for res in sorted(resolutions):
        label = f"✅ {res}×{res}" if res == current else f"{res}×{res}"
        rows.append([_btn(label, f"q:{res}")])
    rows.append([_btn(t(lang, "btn.back"), "m:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bot_settings_menu(lang: str, maintenance: bool, membership: bool) -> InlineKeyboardMarkup:
    m_state = t(lang, "btn.on") if maintenance else t(lang, "btn.off")
    c_state = t(lang, "btn.on") if membership else t(lang, "btn.off")
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"🔧 {m_state}", "bs:maintenance")],
        [_btn(f"🔐 {c_state}", "bs:membership")],
        [_btn(t(lang, "btn.back"), "m:admin")],
    ])


def channel_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t(lang, "btn.back"), "m:admin")],
    ])


def users_page_menu(
    lang: str, offset: int, page_size: int, total: int,
    query: str = "", users: list[dict] | None = None,
) -> InlineKeyboardMarkup:
    """Навигация по списку пользователей: карточки, пагинация, поиск."""
    pages = max(1, (total + page_size - 1) // page_size)
    page = offset // page_size + 1

    rows = []
    for u in (users or []):
        name = u.get("first_name") or u.get("username") or str(u["user_id"])
        star = "⭐ " if u.get("active_plan") else ""
        rows.append([_btn(f"{star}{u['user_id']} · {name}"[:40], f"u:v:{u['user_id']}")])

    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(_btn("⬅️", f"u:p:{max(0, offset - page_size)}"))
    nav.append(_btn(t(lang, "users.page", page=page, pages=pages), "u:noop"))
    if offset + page_size < total:
        nav.append(_btn("➡️", f"u:p:{offset + page_size}"))
    rows.append(nav)

    if query:
        rows.append([_btn(t(lang, "users.clear"), "u:clr")])
    else:
        rows.append([_btn(t(lang, "users.search_btn"), "u:srch")])
    rows.append([_btn(t(lang, "btn.export"), "a:export")])
    rows.append([_btn(t(lang, "btn.back"), "m:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def users_search_cancel(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t(lang, "btn.back"), "a:users")],
    ])


def membership_kb(lang: str, url: str) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([_url_btn(t(lang, "member.join_btn"), url)])
    rows.append([_btn(t(lang, "member.check"), "c:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
