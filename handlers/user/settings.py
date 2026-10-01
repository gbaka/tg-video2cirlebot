"""
Настройки пользователя: язык и качество
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Message,
)

import menus
from access import (
    ensure_user,
    user_plan,
)
from context import get_ctx
from i18n import SUPPORTED, lang_name, t
from services.video_options import parse_fragment
from tg import cb_data, cb_message

logger = logging.getLogger(__name__)
router = Router(name="user.settings")
private_chat = F.chat.type == "private"


class VideoSettings(StatesGroup):
    fragment = State()


def video_settings_text(lang: str, user: dict) -> str:
    mode = (t(lang, "video.mode_fit") if user.get("crop_mode") == "fit"
            else t(lang, "video.mode_crop"))
    duration = user.get("trim_duration")
    fragment = (t(lang, "video.fragment_value", start=user.get("trim_start", 0), duration=duration)
                if duration is not None else t(lang, "video.fragment_none"))
    return t(lang, "video.options", mode=mode, fragment=fragment)


async def apply_fragment(message: Message, user: dict, args: str) -> bool:
    ctx = get_ctx()
    lang = user["language"]
    if args.strip().lower() == "reset":
        await ctx.users.reset_video_fragment(user["user_id"])
        await message.answer(t(lang, "video.fragment_reset"))
        return True
    plan, _ = await user_plan(user)
    fragment = parse_fragment(args, plan.max_duration_sec)
    if fragment is None:
        await message.answer(t(lang, "video.fragment_prompt", limit=plan.max_duration_sec))
        return False
    start, duration = fragment
    await ctx.users.set_video_options(user["user_id"], trim_start=start, trim_duration=duration)
    await message.answer(t(lang, "video.fragment_saved", start=start, duration=duration))
    return True


@router.message(Command("fragment"), private_chat)
async def cmd_fragment(message: Message, command: CommandObject) -> None:
    await apply_fragment(message, await ensure_user(message), command.args or "")


@router.callback_query(F.data == "m:framing")
async def cb_framing(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        t(lang, "video.framing_prompt"),
        reply_markup=menus.framing_menu(lang, user.get("crop_mode", "crop")),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("v:mode:"))
async def cb_set_framing(cb: CallbackQuery) -> None:
    mode = cb_data(cb).split(":")[-1]
    if mode not in {"crop", "fit"}:
        await cb.answer()
        return
    user = await ensure_user(cb)
    await get_ctx().users.set_video_options(user["user_id"], crop_mode=mode)
    lang = user["language"]
    await cb_message(cb).edit_text(
        t(lang, "video.framing_prompt"), reply_markup=menus.framing_menu(lang, mode),
    )
    await cb.answer(t(lang, "video.mode_saved"))


@router.callback_query(F.data == "m:fragment")
async def cb_fragment(cb: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_user(cb)
    plan, _ = await user_plan(user)
    await state.set_state(VideoSettings.fragment)
    await cb_message(cb).edit_text(
        t(user["language"], "video.fragment_prompt", limit=plan.max_duration_sec),
        reply_markup=menus.fragment_menu(user["language"]),
    )
    await cb.answer()


@router.callback_query(F.data == "v:fragment:reset")
async def cb_reset_fragment(cb: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_user(cb)
    await get_ctx().users.reset_video_fragment(user["user_id"])
    await state.clear()
    plan, _ = await user_plan(user)
    await cb_message(cb).edit_text(
        t(user["language"], "video.fragment_reset"),
        reply_markup=menus.settings_menu(user["language"], plan.code == "pro"),
    )
    await cb.answer()


@router.message(VideoSettings.fragment, F.text & ~F.text.startswith("/"), private_chat)
async def msg_fragment(message: Message, state: FSMContext) -> None:
    if await apply_fragment(message, await ensure_user(message), message.text or ""):
        await state.clear()

@router.callback_query(F.data == "m:settings")
async def cb_settings(cb: CallbackQuery, state: FSMContext | None = None) -> None:
    if state is not None:
        await state.clear()
    user = await ensure_user(cb)
    lang = user["language"]
    plan, _ = await user_plan(user)
    quality = (
        t(lang, "profile.quality_selectable", res=plan.normalize_resolution(user.get("quality")))
        if plan.code == "pro"
        else t(lang, "profile.quality_fixed", res=plan.default_resolution)
    )
    text = (t(lang, "settings.title", lang=lang_name(lang), quality=quality)
            + video_settings_text(lang, user))
    if plan.code != "pro":
        text += t(lang, "settings.quality_locked")
    await cb_message(cb).edit_text(
        text, reply_markup=menus.settings_menu(lang, plan.code == "pro"), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "m:lang")
async def cb_lang(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    await cb_message(cb).edit_text(
        t(lang, "lang.title"), reply_markup=menus.language_menu(lang, lang), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("l:"))
async def cb_set_lang(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    new_lang = cb_data(cb).split(":", 1)[1]
    if new_lang not in SUPPORTED:
        await cb.answer()
        return
    user = await ensure_user(cb)
    await ctx.users.set_language(user["user_id"], new_lang)
    await cb.answer(t(new_lang, "lang.changed", lang=lang_name(new_lang)))
    # Перерисовываем меню настроек на новом языке
    user["language"] = new_lang
    plan, _ = await user_plan(user)
    quality = (
        t(new_lang, "profile.quality_selectable",
          res=plan.normalize_resolution(user.get("quality")))
        if plan.code == "pro"
        else t(new_lang, "profile.quality_fixed", res=plan.default_resolution)
    )
    text = (t(new_lang, "settings.title", lang=lang_name(new_lang), quality=quality)
            + video_settings_text(new_lang, user))
    if plan.code != "pro":
        text += t(new_lang, "settings.quality_locked")
    await cb_message(cb).edit_text(
        text,
        reply_markup=menus.settings_menu(new_lang, plan.code == "pro"),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "m:quality")
async def cb_quality(cb: CallbackQuery) -> None:
    user = await ensure_user(cb)
    lang = user["language"]
    plan, _ = await user_plan(user)
    if plan.code != "pro":
        await cb.answer(t(lang, "settings.quality_locked").strip(), show_alert=True)
        return
    current = plan.normalize_resolution(user.get("quality"))
    await cb_message(cb).edit_text(
        t(lang, "quality.title") + "\n\n" + t(lang, "quality.current", res=current),
        reply_markup=menus.quality_menu(lang, plan.resolutions, current),
        parse_mode="HTML",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("q:"))
async def cb_set_quality(cb: CallbackQuery) -> None:
    ctx = get_ctx()
    user = await ensure_user(cb)
    lang = user["language"]
    plan, _ = await user_plan(user)
    if plan.code != "pro":
        await cb.answer(t(lang, "settings.quality_locked").strip(), show_alert=True)
        return
    try:
        res = int(cb_data(cb).split(":", 1)[1])
    except ValueError:
        await cb.answer()
        return
    res = plan.normalize_resolution(res)
    await ctx.users.set_quality(user["user_id"], res)
    await cb.answer(t(lang, "quality.changed", res=res))
    user["quality"] = res
    await cb_message(cb).edit_text(
        t(lang, "quality.title") + "\n\n" + t(lang, "quality.current", res=res),
        reply_markup=menus.quality_menu(lang, plan.resolutions, res),
        parse_mode="HTML",
    )
