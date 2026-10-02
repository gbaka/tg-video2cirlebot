"""
Точка входа Video to Circle Bot.

Собирает конфиг, БД, репозитории, хендлеры и фоновые задачи.
"""

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.fsm.storage.memory import MemoryStorage

import context as ctx_module
from bot_metadata import publish_bot_metadata
from channel_checker import ChannelChecker
from command_hints import publish_command_hints
from config_loader import Config
from context import AppContext
from conversion_queue import ConversionQueue
from db import Database
from handlers.admin import router as admin_router
from handlers.user import router as user_router
from healthcheck import write_heartbeat
from i18n import load_locales
from repositories import (
    ChannelRepo,
    PaymentRepo,
    SettingsRepo,
    SubscriptionRepo,
    SupportRepo,
    UsageRepo,
    UserRepo,
)
from repositories.conversion_cache import ConversionCacheRepo
from tasks import JobRunner, TaskRegistry
from tempfiles import TempFiles
from video_converter import VideoConverter

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Пути считаем от расположения файла, а не от текущего каталога: иначе запуск
# бота из другого каталога молча не находил бы конфиг и локали.
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
LOCALES_DIR = BASE_DIR / "locales"


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
    if ctx_module.ctx is None or ctx_module.ctx.tempfiles is None:
        return
    removed = ctx_module.ctx.tempfiles.cleanup(max_age_sec=3600)
    if removed:
        logger.info("Очищено каталогов задач: %d", removed)


async def job_heartbeat() -> None:
    """Only a responsive app with readable application tables publishes readiness."""
    if ctx_module.ctx is None:
        return
    async with (
        ctx_module.ctx.db.connect() as connection,
        connection.execute("SELECT 1 FROM users LIMIT 1") as cursor,
    ):
        await cursor.fetchone()
    write_heartbeat(BASE_DIR / "data" / "health.json")


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


def build_bot(config: Config) -> Bot:
    session = None
    if config.api.local:
        # Local Bot API files must be shared with this process at the same paths.
        session = AiohttpSession(api=TelegramAPIServer.from_base(
            config.api.base_url.rstrip("/"), is_local=True,
        ))
    return Bot(
        token=config.bot.token, session=session,
        default=DefaultBotProperties(parse_mode="HTML"),
    )


async def main() -> int:
    try:
        config = Config.load(CONFIG_PATH)
    except (FileNotFoundError, ValueError) as e:
        logger.error("%s", e)
        logger.error("Проверьте config.yaml по примеру config.example.yaml")
        return 1

    errors = config.validate()
    if errors:
        for problem in errors:
            logger.error(problem)
        return 1
    for note in config.warnings():
        logger.warning(note)

    logging.getLogger().setLevel(config.logging.level)
    load_locales(LOCALES_DIR)

    bot = build_bot(config)
    try:
        return await run_bot(config, bot)
    finally:
        await bot.session.close()


async def shutdown_context(ctx: AppContext) -> None:
    """All drains share one budget; the session stays open until accepted work ends."""
    deadline = asyncio.get_running_loop().time() + ctx.config.processing.shutdown_timeout_sec
    await asyncio.sleep(0)  # let already-created update tasks enter the middleware
    ctx.requests.close()
    for name, cleanup in (
        ("albums", ctx.albums.shutdown),
        ("queue", ctx.conversion_queue.shutdown),
        ("requests", ctx.requests.shutdown),
    ):
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            await cleanup(timeout=remaining)
        except Exception:
            logger.exception("Shutdown failed: %s", name)
    await ctx.jobs.stop()


async def run_bot(config: Config, bot: Bot) -> int:
    db = Database(str(BASE_DIR / "data" / "bot_data.db"))
    health_path = BASE_DIR / "data" / "health.json"
    health_path.unlink(missing_ok=True)
    await db.init()
    channel_link = await ChannelRepo(db).get_link()
    temp_root = Path(config.processing.temp_dir)
    if not temp_root.is_absolute():
        temp_root = BASE_DIR / temp_root
    ctx = AppContext(
        config=config, plans=config.plans, db=db,
        users=UserRepo(db), subs=SubscriptionRepo(db), usage=UsageRepo(db),
        settings=SettingsRepo(db), channel=ChannelRepo(db), payments=PaymentRepo(db),
        support=SupportRepo(db),
        tasks=TaskRegistry(), jobs=JobRunner(), bot=bot,
        channel_checker=ChannelChecker(bot, channel_link),
        converter=VideoConverter(
            probe_timeout_sec=config.processing.probe_timeout_sec,
            encode_timeout_sec=config.processing.encode_timeout_sec,
            threads=config.processing.effective_threads(),
        ),
        conversion_queue=ConversionQueue(
            workers=config.processing.workers, queue_size=config.processing.queue_size,
        ),
        conversion_cache=ConversionCacheRepo(db), tempfiles=TempFiles(temp_root),
    )
    ctx_module.ctx = ctx
    dp = None
    try:
        ctx.jobs.register("expire_subscriptions", job_expire_subscriptions, interval=600)
        ctx.jobs.register("cleanup_temp", job_cleanup_temp, interval=1800)
        ctx.jobs.register("daily_stats", job_daily_stats, interval=3600)
        ctx.jobs.register("heartbeat", job_heartbeat, interval=10)
        dp = build_dispatcher()
        dp.update.outer_middleware(ctx.requests)
        me = await bot.get_me()
        logger.info("Бот @%s запущен", me.username)
        logger.info(
            "Конвертация: потоков на задачу %d, параллельных задач %d",
            config.processing.effective_threads(), config.processing.workers,
        )
        try:
            failed = await publish_command_hints(
                bot, config.bot.admin_ids, fallback=config.default_language,
            )
        except Exception:  # подсказки не должны мешать запуску
            logger.exception("Публикация подсказок команд не удалась")
        else:
            if failed:
                logger.warning("Подсказки команд применены не полностью: %s", failed)
        try:
            failed = await publish_bot_metadata(bot, fallback=config.default_language)
        except Exception:  # витрина бота не должна мешать запуску
            logger.exception("Публикация описания бота не удалась")
        else:
            if failed:
                logger.warning("Описание бота применено не полностью: %s", failed)
        await job_heartbeat()
        await ctx.jobs.start()
        await dp.start_polling(
            bot, allowed_updates=dp.resolve_used_update_types(), close_bot_session=False,
        )
        return 0
    finally:
        logger.info("Остановка бота…")
        try:
            await shutdown_context(ctx)
        finally:
            health_path.unlink(missing_ok=True)
            ctx_module.ctx = None
            if dp is not None:
                await dp.storage.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
