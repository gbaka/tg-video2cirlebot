"""
Хелперы для объектов aiogram, сужающие типы.

В типах aiogram у callback-а есть и сообщение (`Message | InaccessibleMessage | None`),
и данные (`str | None`). Для наших кнопок они всегда заполнены, но статический
анализ этого не знает — эти функции явно проверяют и сужают тип.
"""

from aiogram.types import CallbackQuery, Message


def cb_message(cb: CallbackQuery) -> Message:
    """Сообщение, к которому привязана кнопка."""
    msg = cb.message
    if not isinstance(msg, Message):
        raise RuntimeError("Callback без доступного сообщения")
    return msg


def cb_data(cb: CallbackQuery) -> str:
    """Данные callback-а."""
    if cb.data is None:
        raise RuntimeError("Callback без данных")
    return cb.data
