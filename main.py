"""
Точка входа Video to Circle Bot.

Собирает конфиг, БД, репозитории, хендлеры и фоновые задачи.
"""

import asyncio
import logging
import os
import tempfile

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

import context as ctx_module
from channel_checker import ChannelChecker
from config_loader import Config
from context import AppContext, get_ctx
from db import Database
from handlers_admin import router as admin_router
from handlers_user import router as user_router
from i18n import load_locales
from repositories import (
    ChannelRepo,
    PaymentRepo,
    SettingsRepo,
    SubscriptionRepo,
    UsageRepo,
    UserRepo,
)
from tasks import JobRunner, TaskRegistry
from video_converter import VideoConverter

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# ===== Фоновые задачи =====

async def job_expire_subscriptions() -> None:
    """Помечает просроченные подписки неактивными."""
    if ctx_module.ctx is None:
        return
    n = await ctx_module.ctx.subs.expire_due()
    if n:
        logger.info("Истекло подписок: %d", n)


async def job_cleanup_temp() -> None:
    """Чистит временные файлы конвертации старше 1 часа."""
    tmp_dir = tempfile.gettempdir()
    removed = 0
    import time as _time
    now = _time.time()
    try:
        for name in os.listdir(tmp_dir):
            path = os.path.join(tmp_dir, name)
            try:
                if os.path.isfile(path) and now - os.path.getmtime(path) > 3600:
                    os.unlink(path)
                    removed += 1
            except OSError:
                continue
    except FileNotFoundError:
        return
    if removed:
        logger.info("Очищено временных файлов: %d", removed)


async def job_daily_stats() -> None:
    """Периодический снимок статистики в лог."""
    if ctx_module.ctx is None:
        return
    c = ctx_module.ctx
    users = await c.users.count_all()
    total = await c.usage.count_all()
    subs = await c.subs.count_active()
    logger.info("Снимок: users=%d, conversions=%d, active_subs=%d", users, total, subs)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)   # админ-команды регистрируем раньше catch-all
    dp.include_router(user_router)
    return dp


async def main() -> None:
    config = Config.load("config.yaml")
    errors = config.validate()
    if errors:
        for e in errors:
            logger.error(e)
        return

    logging.getLogger().setLevel(config.logging.level)
    load_locales("locales")

    db = Database("/app/data/bot_data.db")
    await db.init()

    bot = Bot(token=config.bot.token, default=DefaultBotProperties(parse_mode="HTML"))
    channel_link = await ChannelRepo(db).get_link()

    ctx_module.ctx = AppContext(
        config=config,
        plans=config.plans,
        db=db,
        users=UserRepo(db),
        subs=SubscriptionRepo(db),
        usage=UsageRepo(db),
        settings=SettingsRepo(db),
        channel=ChannelRepo(db),
        payments=PaymentRepo(db),
        tasks=TaskRegistry(),
        jobs=JobRunner(),
        bot=bot,
        channel_checker=ChannelChecker(bot, channel_link),
        converter=VideoConverter(),
    )
    ctx = get_ctx()

    # Фоновые задачи
    ctx.jobs.register("expire_subscriptions", job_expire_subscriptions, interval=600)
    ctx.jobs.register("cleanup_temp", job_cleanup_temp, interval=1800)
    ctx.jobs.register("daily_stats", job_daily_stats, interval=3600)

    dp = build_dispatcher()

    @dp.startup()
    async def on_startup() -> None:
        me = await bot.get_me()
        logger.info("Бот @%s запущен", me.username)
        await ctx.jobs.start()

    @dp.shutdown()
    async def on_shutdown() -> None:
        logger.info("Остановка бота…")
        await ctx.jobs.stop()

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
