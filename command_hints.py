"""Программная публикация подсказок команд (меню «/» в клиенте Telegram).

Список команд живёт на серверах Telegram, а не в боте: клиент рисует меню из
того, что записано через setMyCommands. BotFather умеет писать только
default-scope, поэтому отдельный список для администраторов задаётся лишь
отсюда — через scope конкретного чата (у личного чата chat_id == user_id).

Scope выбирается Telegram по самому конкретному совпадению:
chat+язык → chat → all_private_chats+язык → all_private_chats → default+язык → default.
Поэтому базовый список публикуется и в default, и в all_private_chats: иначе
оставшийся от ручных настроек all_private_chats перекрыл бы default+ru.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

from i18n import t

logger = logging.getLogger(__name__)

# Порядок = порядок в меню. Команда обязана иметь ключ cmd.<имя> в локалях:
# tests/test_command_hints.py сверяет списки с зарегистрированными Command(...).
USER_COMMANDS = (
    "start",
    "menu",
    "help",
    "setlanguage",
    "fragment",
    "terms",
    "paysupport",
)
ADMIN_COMMANDS = (
    *USER_COMMANDS,
    "admin",
    "stats",
    "tasks",
    "finduser",
    "gift",
    "revoke",
    "exportusers",
    "setchannel",
    "channel",
    "clearchannel",
    "supportreply",
    "syncmenu",
)

BASE_SCOPES = (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats())
# Языки подсказок плюс вариант без language_code как запасной для прочих языков.
LANGS = (None, "ru", "en")


def build(commands, lang: str | None) -> list[BotCommand]:
    """Собирает список команд с описаниями из локалей."""
    language = lang or "ru"
    return [
        BotCommand(command=name, description=t(language, f"cmd.{name}"))
        for name in commands
    ]


async def publish_command_hints(bot: Bot, admin_ids) -> list[str]:
    """Пишет базовый список всем и расширенный — в личный чат каждого админа.

    Возвращает список того, что применить не удалось (например, админ ещё не
    открывал бота: `chat not found`). Публикация не должна ронять запуск бота —
    это подсказки, а не рабочий путь.
    """
    failed: list[str] = []
    for lang in LANGS:
        commands = build(USER_COMMANDS, lang)
        for scope in BASE_SCOPES:
            try:
                await bot.set_my_commands(commands, scope=scope, language_code=lang)
            except TelegramAPIError as e:
                failed.append(f"{type(scope).__name__}/{lang or 'default'}: {e}")
                logger.warning("Подсказки не опубликованы (%s, %s): %s",
                               type(scope).__name__, lang, e)
    for admin_id in admin_ids:
        for lang in LANGS:
            commands = build(ADMIN_COMMANDS, lang)
            try:
                await bot.set_my_commands(
                    commands,
                    scope=BotCommandScopeChat(chat_id=admin_id),
                    language_code=lang,
                )
            except TelegramAPIError as e:
                failed.append(f"admin {admin_id}/{lang or 'default'}: {e}")
                logger.warning("Подсказки для админа %s не опубликованы: %s", admin_id, e)
    if not failed:
        logger.info("Подсказки команд опубликованы (админов: %d)", len(admin_ids))
    return failed
