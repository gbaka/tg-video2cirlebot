"""
Общее для админских хендлеров: константы, состояния FSM, проверка прав.

Модули пакета `handlers.admin` собираются в один роутер в `__init__.py`.
"""

from aiogram import F
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from access import is_admin
from i18n import t

private_chat = F.chat.type == "private"

USERS_PAGE_SIZE = 10
EXPORT_PAGE = 500
GIFT_DEFAULT_DAYS = 30
LIFETIME_WORDS = {"life", "lifetime", "forever", "навсегда", "вечно"}


class AdminUsers(StatesGroup):
    """Ввод поискового запроса по пользователям."""
    search = State()


async def admin_guard(event: Message | CallbackQuery) -> bool:
    """Пускает дальше только администраторов, остальным отвечает отказом."""
    actor = event.from_user
    if actor is None or not is_admin(actor.id):
        if isinstance(event, CallbackQuery):
            await event.answer(t("ru", "admin.denied"), show_alert=True)
        else:
            await event.answer(t("ru", "admin.denied"))
        return False
    return True
