"""
Пользовательские хендлеры.

Порядок `include_router` повторяет порядок регистрации обработчиков из
прежнего монолитного `handlers_user.py`. `media` подключается последним:
в нём есть catch-all `@router.message(private_chat)`.
"""

from aiogram import Router

from handlers.user import billing, media, menu, settings

router = Router(name="user")
router.include_router(menu.router)
router.include_router(settings.router)
router.include_router(billing.router)
router.include_router(media.router)
