"""
Админские хендлеры.

Порядок `include_router` повторяет порядок регистрации обработчиков из
прежнего монолитного `handlers_admin.py` — aiogram проверяет роутеры
по очереди, и порядок важен для пересекающихся фильтров.
"""

from aiogram import Router

from handlers.admin import cards, channel, export, menu, settings, stats, users

router = Router(name="admin")
router.include_router(menu.router)
router.include_router(stats.router)
router.include_router(users.router)
router.include_router(settings.router)
router.include_router(channel.router)
router.include_router(cards.router)
router.include_router(export.router)
