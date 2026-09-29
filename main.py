"""
Главный модуль Telegram бота для конвертации видео в кружки.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config_loader import Config
from video_converter import VideoConverter
from channel_checker import ChannelChecker
from storage import ChannelStorage

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные объекты (инициализируются в main)
config: Config
bot: Bot
dp: Dispatcher
converter: VideoConverter
channel_checker: ChannelChecker
channel_storage: ChannelStorage


class AdminStates(StatesGroup):
    """Состояния для админских команд."""
    waiting_channel_link = State()


def is_admin(user_id: int) -> bool:
    """Проверка прав администратора."""
    return user_id in config.bot.admin_ids


def get_join_keyboard(invite_url: str, invite_text: str) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой вступления в канал."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=invite_text, url=invite_url)]
    ])


async def check_subscription(message: Message) -> bool:
    """
    Проверяет подписку пользователя на канал.
    Возвращает True если проверка пройдена или не требуется.
    """
    # Админы проходят всегда
    if is_admin(message.from_user.id):
        return True

    # Получаем актуальную ссылку из БД
    channel_link = await channel_storage.get_link()
    if not channel_link:
        return True

    # Обновляем channel_checker если ссылка изменилась
    if channel_checker.channel_link != channel_link:
        channel_checker.channel_link = channel_link
        channel_checker._parse_channel_link()

    # Проверяем подписку
    is_member = await channel_checker.is_member(message.from_user.id)
    if is_member:
        return True

    # Не подписан — отправляем сообщение с кнопкой
    invite_url = await channel_checker.get_chat_invite_link()
    if not invite_url:
        invite_url = channel_checker.get_join_url()

    await message.answer(
        "🚫 <b>Доступ запрещен</b>\n\n"
        "Для использования бота необходимо вступить в наш канал/чат.",
        reply_markup=get_join_keyboard(invite_url, config.channel.invite_text),
        parse_mode="HTML"
    )
    return False


# ===== Хендлеры =====

router = Router()

# Фильтр: только личные чаты
private_chat = F.chat.type == "private"


@router.message(Command("start"), private_chat)
async def cmd_start(message: Message) -> None:
    """Команда /start."""
    if not await check_subscription(message):
        return

    await message.answer(
        "🎬 <b>Video to Circle Bot</b>\n\n"
        "Отправьте мне видео — я конвертирую его в «кружок» (Video Note) для Telegram.\n\n"
        "📋 <b>Поддерживаемые форматы:</b> практически все (mp4, mov, avi, mkv, webm, flv, wmv, 3gp и др.)\n"
        f"📏 <b>Макс. размер:</b> {config.video.max_size_mb} MB\n"
        f"🎯 <b>Разрешение кружка:</b> {config.video.target_resolution}x{config.video.target_resolution}\n\n"
        "Просто пришлите видео файл или видео-сообщение!",
        parse_mode="HTML"
    )


@router.message(Command("help"), private_chat)
async def cmd_help(message: Message) -> None:
    """Команда /help."""
    if not await check_subscription(message):
        return

    is_user_admin = is_admin(message.from_user.id)

    text = (
        "ℹ️ <b>Video to Circle Bot — Помощь</b>\n\n"
        "<b>Что делает бот:</b>\n"
        "Конвертирует ваши видео в «кружки» (Video Notes) — круглые видео-сообщения Telegram.\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Отправьте видео (файл, видео-сообщение или документ с видео)\n"
        "2. Дождитесь конвертации\n"
        "3. Получите готовый кружок\n\n"
        "<b>📋 Поддерживаемые форматы:</b>\n"
        "MP4, MOV, AVI, MKV, WebM, FLV, WMV, 3GP, 3G2, TS, MTS, M2TS, VOB, OGV, "
        "MXF, F4V, ASF, RM, RMVB, DIVX, XVID, MPG, MPEG, MPE, MPV, M2V, M1V, "
        "DV, DIF, DVF, NSV, ROQ, AMV, QT и другие (через ffmpeg)\n\n"
        "<b>⚙️ Ограничения Telegram для кружков:</b>\n"
        "• Квадратное видео (1:1), разрешение 360×360\n"
        "• Макс. длительность: ~60 секунд\n"
        "• Макс. размер файла: 50 MB (настраивается)\n"
        "• Кодеки: H.264 (Baseline/High), AAC аудио\n\n"
        "<b>🔒 Приватность:</b> Бот работает только в личных сообщениях. В группах не отвечает."
    )

    if is_user_admin:
        text += (
            "\n\n<b>👑 Админ-команды (скрыты от обычных пользователей):</b>\n"
            "<code>/setchannel &lt;ссылка&gt;</code> — установить канал/чат для проверки подписки\n"
            "<code>/channel</code> — показать текущий канал\n"
            "<code>/clearchannel</code> — отключить проверку подписки\n\n"
            "<b>Форматы ссылок:</b>\n"
            "• <code>@username</code> — публичный канал/чат\n"
            "• <code>-1001234567890</code> — числовой ID\n"
            "• <code>https://t.me/username</code> — ссылка на канал/чат\n"
            "• <code>https://t.me/c/1234567890</code> — приватный канал/чат по ID"
        )

    await message.answer(text, parse_mode="HTML")


@router.message(Command("setchannel"), private_chat)
async def cmd_set_channel(message: Message, command: CommandObject, state: FSMContext) -> None:
    """Установка канала для проверки подписки (только админы)."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет прав администратора.")
        return

    args = command.args
    if not args:
        current_link = await channel_storage.get_link()
        await message.answer(
            "📝 <b>Установка канала/чата</b>\n\n"
            "Использование: <code>/setchannel &lt;ссылка&gt;</code>\n\n"
            "<b>Поддерживаемые форматы:</b>\n"
            "• <code>@username</code> — публичный канал/чат\n"
            "• <code>-1001234567890</code> — числовой ID\n"
            "• <code>https://t.me/username</code> — ссылка на канал/чат\n"
            "• <code>https://t.me/c/1234567890</code> — приватный канал/чат по ID\n\n"
            "Текущий канал: " + (current_link or "не задан"),
            parse_mode="HTML"
        )
        return

    new_link = args.strip()

    # Проверяем, что бот может получить доступ к каналу
    old_link = channel_checker.channel_link
    channel_checker.channel_link = new_link
    channel_checker._parse_channel_link()  # Перепарсим

    try:
        chat = await bot.get_chat(channel_checker.chat_id)
        # Сохраняем в SQLite
        await channel_storage.set_link(new_link)
        await message.answer(
            f"✅ <b>Канал/чат обновлен</b>\n\n"
            f"📛 Название: {chat.title}\n"
            f"🔗 Ссылка: {new_link}\n"
            f"👥 Участников: {getattr(chat, 'participants_count', '?')}\n"
            f"🔐 Тип: {chat.type}",
            parse_mode="HTML"
        )
    except Exception as e:
        channel_checker.channel_link = old_link
        channel_checker._parse_channel_link()
        await message.answer(f"❌ Ошибка: не удалось получить доступ к каналу/чату.\n<code>{e}</code>", parse_mode="HTML")


@router.message(Command("channel"), private_chat)
async def cmd_show_channel(message: Message) -> None:
    """Показать текущий канал (только админы)."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет прав администратора.")
        return

    current_link = await channel_storage.get_link()
    if not current_link:
        await message.answer("📭 Канал для проверки подписки не задан.")
        return

    try:
        chat = await bot.get_chat(channel_checker.chat_id)
        await message.answer(
            f"📋 <b>Текущий канал/чат</b>\n\n"
            f"📛 Название: {chat.title}\n"
            f"🔗 Ссылка: {current_link}\n"
            f"🆔 ID: {chat.id}\n"
            f"👥 Участников: {getattr(chat, 'participants_count', '?')}\n"
            f"🔐 Тип: {chat.type}",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка получения информации: <code>{e}</code>", parse_mode="HTML")


@router.message(Command("clearchannel"), private_chat)
async def cmd_clear_channel(message: Message) -> None:
    """Отключить проверку подписки (только админы)."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет прав администратора.")
        return

    await channel_storage.clear_link()
    channel_checker.channel_link = ""
    channel_checker._parse_channel_link()
    await message.answer("✅ Проверка подписки отключена.")


@router.message(F.video | F.video_note | F.document, private_chat)
async def handle_video(message: Message) -> None:
    """Обработка входящих видео."""
    if not await check_subscription(message):
        return

    # Определяем файл
    file_id = None
    file_name = "video"
    file_size = 0

    if message.video:
        file_id = message.video.file_id
        file_name = message.video.file_name or f"video_{message.video.file_unique_id}.mp4"
        file_size = message.video.file_size
    elif message.video_note:
        file_id = message.video_note.file_id
        file_name = f"circle_{message.video_note.file_unique_id}.mp4"
        file_size = message.video_note.file_size
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("video/"):
        file_id = message.document.file_id
        file_name = message.document.file_name or f"video_{message.document.file_unique_id}"
        file_size = message.document.file_size
    else:
        return

    # Проверка размера
    max_bytes = config.video.max_size_mb * 1024 * 1024
    if file_size > max_bytes:
        await message.answer(
            f"❌ Файл слишком большой: {file_size / 1024 / 1024:.1f} MB\n"
            f"Максимум: {config.video.max_size_mb} MB"
        )
        return

    # Проверка формата
    if not VideoConverter.is_supported(file_name):
        await message.answer(f"❌ Неподдерживаемый формат: {Path(file_name).suffix}")
        return

    status_msg = await message.answer("⏳ Скачиваю видео...")

    try:
        # Скачиваем во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_name).suffix) as tmp_in:
            await bot.download(file_id, destination=tmp_in.name)
            input_path = tmp_in.name

        await status_msg.edit_text("🔄 Конвертирую в кружок...")

        # Конвертируем
        output_path, metadata = await converter.convert_to_circle(input_path)

        await status_msg.edit_text("📤 Отправляю кружок...")

        # Отправляем как video_note
        with open(output_path, "rb") as f:
            video_data = f.read()

        video_note = BufferedInputFile(video_data, filename="circle.mp4")
        await message.answer_video_note(
            video_note,
            duration=int(metadata['duration']),
            length=metadata['width']  # ширина = высота для кружка
        )

        await status_msg.delete()

        # Удаляем временные файлы
        os.unlink(input_path)
        os.unlink(output_path)

    except ValueError as e:
        await status_msg.edit_text(f"❌ {e}")
        if 'input_path' in locals() and os.path.exists(input_path):
            os.unlink(input_path)
    except RuntimeError as e:
        await status_msg.edit_text(f"❌ Ошибка конвертации: {e}")
        if 'input_path' in locals() and os.path.exists(input_path):
            os.unlink(input_path)
    except TelegramBadRequest as e:
        if "video note" in str(e).lower() or "duration" in str(e).lower():
            await status_msg.edit_text(
                "❌ Видео слишком длинное для кружка (макс. ~60 сек).\n"
                "Попробуйте отправить более короткое видео."
            )
        else:
            await status_msg.edit_text(f"❌ Ошибка Telegram: {e}")
    except Exception as e:
        logger.exception("Unexpected error")
        await status_msg.edit_text(f"❌ Неожиданная ошибка: {e}")


@router.message(private_chat)
async def handle_other(message: Message) -> None:
    """Остальные сообщения."""
    if not await check_subscription(message):
        return

    await message.answer(
        "📹 Пришлите видео-файл, видео-сообщение или документ с видео — "
        "я сделаю из него кружок!"
    )


async def on_startup() -> None:
    """Действия при запуске."""
    logger.info("Bot starting...")
    me = await bot.get_me()
    logger.info(f"Bot @{me.username} started")


async def on_shutdown() -> None:
    """Действия при остановке."""
    logger.info("Bot stopping...")


async def main() -> None:
    global config, bot, dp, converter, channel_checker, channel_storage

    # Загружаем конфиг
    config = Config.load("config.yaml")
    errors = config.validate()
    if errors:
        for err in errors:
            logger.error(err)
        return

    # Настраиваем уровень логирования
    logging.getLogger().setLevel(config.logging.level)

    # Инициализируем хранилище и загружаем ссылку на канал
    channel_storage = ChannelStorage("/app/data/bot_data.db")
    await channel_storage.init()
    initial_link = await channel_storage.get_link()

    # Инициализируем компоненты
    bot = Bot(token=config.bot.token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    converter = VideoConverter(
        target_resolution=config.video.target_resolution,
        crf=config.video.crf,
        preset=config.video.preset,
        max_size_mb=config.video.max_size_mb
    )

    channel_checker = ChannelChecker(bot, initial_link)

    # Регистрируем startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Запускаем polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())