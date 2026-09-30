"""
Утилиты для проверки подписки на канал/чат.
"""

import contextlib
import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)


class ChannelChecker:
    """Проверка участия пользователя в канале/чате."""

    def __init__(self, bot: Bot, channel_link: str = ""):
        self.bot = bot
        self.channel_link = channel_link
        self._channel_id: int | None = None
        self._channel_username: str | None = None
        self._parse_channel_link()

    def set_link(self, link: str) -> None:
        """Меняет проверяемый канал и сбрасывает разобранный кэш."""
        self.channel_link = link or ""
        self._channel_id = None
        self._channel_username = None
        self._parse_channel_link()

    def _parse_channel_link(self) -> None:
        """Парсит ссылку на канал для получения ID или username."""
        if not self.channel_link:
            return

        link = self.channel_link.strip()

        # @username
        if link.startswith("@"):
            self._channel_username = link[1:]
            return

        # https://t.me/username или https://t.me/c/123456789
        if "t.me/" in link:
            parts = link.split("t.me/")[-1].split("/")
            if parts[0] == "c" and len(parts) > 1:
                # Приватный канал/чат по ID: https://t.me/c/123456789
                # Telegram ID = -100 + numeric_id
                with contextlib.suppress(ValueError):
                    self._channel_id = -100 * 10**9 - int(parts[1])  # -1001234567890
            else:
                # Публичный username
                self._channel_username = parts[0].split("?")[0]
            return

        # Числовой ID (может быть с -100 префиксом или без)
        try:
            parsed_id = int(link)
            # Если ID положительный и похож на краткий ID приватного чата — добавляем -100
            if parsed_id > 0 and parsed_id < 10**10:
                self._channel_id = -100 * 10**9 - parsed_id
            else:
                self._channel_id = parsed_id
        except ValueError:
            pass

    @property
    def chat_id(self) -> int | str | None:
        """Возвращает идентификатор чата для API вызовов."""
        if self._channel_id:
            return self._channel_id
        if self._channel_username:
            return f"@{self._channel_username}"
        return None

    async def is_member(self, user_id: int) -> bool:
        """
        Проверяет, является ли пользователь участником канала/чата.

        Returns:
            True если пользователь участник/админ/создатель, False если не участник.
            Если бот не может проверить (нет доступа или он не участник чата),
            проверка пропускается и возвращается True.
        """
        if not self.chat_id:
            logger.warning("Channel link not configured, skipping check")
            return True

        try:
            member = await self.bot.get_chat_member(chat_id=self.chat_id, user_id=user_id)
            return member.status in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR
            )
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "chat not found" in err:
                logger.error(f"Канал/чат не найден: {self.channel_link}")
            elif "user not found" in err:
                return False
            elif "chat_admin_required" in err or "not enough rights" in err:
                # Бот не админ или не участник — не можем проверить, пропускаем
                logger.warning(
                    "Недостаточно прав для проверки подписки в %s, пропускаем проверку",
                    self.channel_link,
                )
                return True
            else:
                logger.error(f"Ошибка проверки подписки: {e}")
            return False
        except TelegramForbiddenError:
            # Бот не участник чата — не можем проверить
            logger.warning(
                "Бот не участник чата %s, пропускаем проверку подписки", self.channel_link
            )
            return True
        except Exception as e:
            logger.error(f"Неожиданная ошибка проверки подписки: {e}")
            return False

    async def get_chat_invite_link(self) -> str | None:
        """Получает invite link канала/чата (если бот админ)."""
        if not self.chat_id:
            return None

        try:
            chat = await self.bot.get_chat(self.chat_id)
            if chat.invite_link:
                return chat.invite_link
            # Пробуем создать ссылку, если бот администратор
            link = await self.bot.create_chat_invite_link(self.chat_id)
            return link.invite_link
        except Exception as e:
            logger.warning(f"Не удалось получить invite link: {e}")
            return None

    def get_join_url(self) -> str:
        """Возвращает URL для вступления в канал/чат."""
        if self._channel_username:
            return f"https://t.me/{self._channel_username}"
        if self._channel_id:
            # Для приватных чатов нужен invite link (бот должен быть админом)
            chat_id = abs(self._channel_id)
            # Внутренний id чата без префикса -100
            internal_id = chat_id // 10**9 * 10**9 + chat_id % 10**9
            return f"https://t.me/c/{internal_id}"
        return self.channel_link
