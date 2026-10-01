"""
Список пользователей: пагинация, поиск
"""

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import menus
from access import ensure_user
from context import get_ctx
from handlers.admin.shared import (
    USERS_PAGE_SIZE,
    AdminUsers,
    admin_guard,
    private_chat,
)
from i18n import t
from tg import cb_data, cb_message
from timeutil import ago

logger = logging.getLogger(__name__)
router = Router(name="admin.users")

async def _render_users(
    lang: str, offset: int, query: str = ""
) -> tuple[str, InlineKeyboardMarkup]:
    ctx = get_ctx()
    users, total = await ctx.users.search_page(offset, USERS_PAGE_SIZE, query)

    text = t(lang, "users.title") + t(
        lang, "users.body",
        total=total,
        new_day=await ctx.users.count_since(ago(days=1)),
    )
    if query:
        text += t(lang, "users.for_query", query=escape(query))

    if not users:
        text += t(lang, "users.nothing")
    else:
        for u in users:
            plan = "⭐ Pro" if u.get("active_plan") else "Free"
            text += t(
                lang, "users.item",
                user_id=u["user_id"],
                name=escape((u.get("first_name") or u.get("username") or "—")[:80]),
                plan=plan,
                created=(u.get("created_at") or "")[:10],
            )
        shown_from = offset + 1
        shown_to = offset + len(users)
        text += t(lang, "users.showing", start=shown_from, end=shown_to, total=total)

    markup = menus.users_page_menu(lang, offset, USERS_PAGE_SIZE, total, query, users)
    return text, markup


@router.callback_query(F.data == "a:users")
async def cb_users(cb: CallbackQuery, state: FSMContext) -> None:
    if not await admin_guard(cb):
        return
    await state.clear()
    user = await ensure_user(cb)
    lang = user["language"]
    text, markup = await _render_users(lang, 0)
    await cb_message(cb).edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data.startswith("u:p:"))
async def cb_users_page(cb: CallbackQuery, state: FSMContext) -> None:
    if not await admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    try:
        offset = max(0, int(cb_data(cb).split(":")[2]))
    except (IndexError, ValueError):
        await cb.answer()
        return
    data = await state.get_data()
    text, markup = await _render_users(lang, offset, data.get("query", ""))
    await cb_message(cb).edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "u:noop")
async def cb_users_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data == "u:clr")
async def cb_users_clear(cb: CallbackQuery, state: FSMContext) -> None:
    if not await admin_guard(cb):
        return
    await state.clear()
    user = await ensure_user(cb)
    text, markup = await _render_users(user["language"], 0)
    await cb_message(cb).edit_text(text, reply_markup=markup, parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "u:srch")
async def cb_users_search(cb: CallbackQuery, state: FSMContext) -> None:
    if not await admin_guard(cb):
        return
    user = await ensure_user(cb)
    lang = user["language"]
    await state.set_state(AdminUsers.search)
    await cb_message(cb).edit_text(
        t(lang, "users.search_prompt"),
        reply_markup=menus.users_search_cancel(lang),
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(AdminUsers.search, private_chat)
async def msg_users_search(message: Message, state: FSMContext) -> None:
    if not await admin_guard(message):
        return
    user = await ensure_user(message)
    lang = user["language"]
    query = (message.text or "").strip()[:100]
    await state.update_data(query=query)
    await state.set_state(None)
    text, markup = await _render_users(lang, 0, query)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.message(Command("finduser"), private_chat)
async def cmd_find_user(
    message: Message, command: CommandObject, state: FSMContext,
) -> None:
    if not await admin_guard(message):
        return
    user = await ensure_user(message)
    lang = user["language"]
    query = (command.args or "").strip()[:100]
    await state.clear()
    await state.update_data(query=query)
    text, markup = await _render_users(lang, 0, query)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")
