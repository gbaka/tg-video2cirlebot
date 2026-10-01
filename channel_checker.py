"""
Утилиты для проверки подписки на канал/чат.
"""

import logging
import re
from urllib.parse import urlsplit

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
        self._invite_link: str | None = None
        self._parse_channel_link()

    def set_link(self, link: str) -> None:
        """Меняет проверяемый канал и сбрасывает разобранный кэш."""
        self.channel_link = link or ""
        self._channel_id = None
        self._channel_username = None
        self._invite_link = None
        self._parse_channel_link()

    def _parse_channel_link(self) -> None:
        """Validate the original identifier before deriving a Bot API ID."""
        link = self.channel_link
        username = r"[A-Za-z][A-Za-z0-9_]{4,31}"
        if re.fullmatch(r"@" + username, link):
            self._channel_username = link[1:]
            return
        if re.fullmatch(r"-?[0-9]+", link):
            number = int(link)
            if 1 <= number <= 997852516352:
                self._channel_id = -(10**12 + number)
            elif (-999999999999 <= number <= -1
                  or -1997852516352 <= number <= -1000000000001):
                self._channel_id = number
            return
        try:
            url = urlsplit(link)
        except ValueError:
            return
        if (url.scheme != "https" or url.netloc != "t.me"
                or url.query or url.fragment):
            return
        if re.fullmatch(r"/" + username, url.path):
            self._channel_username = url.path[1:]
        elif re.fullmatch(r"/c/[0-9]+", url.path):
            number = int(url.path.split("/")[-1])
            if 1 <= number <= 997852516352:
                self._channel_id = -(10**12 + number)

    @staticmethod
    def valid_invite_link(link: str) -> bool:
        return bool(re.fullmatch(r"https://t\.me/(?:\+|joinchat/)[A-Za-z0-9_-]{5,128}", link))

    def set_invite_link(self, link: str | None) -> None:
        if link and not self.valid_invite_link(link):
            raise ValueError("Invalid Telegram invite link")
        self._invite_link = link or None

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
            if member.status == ChatMemberStatus.RESTRICTED:
                return bool(getattr(member, "is_member", False))
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

        if self._invite_link:
            return self._invite_link
        try:
            chat = await self.bot.get_chat(self.chat_id)
            link = chat.invite_link
            if isinstance(link, str) and self.valid_invite_link(link):
                self._invite_link = link
                return link
            return None
        except Exception as e:
            logger.warning(f"Не удалось получить invite link: {e}")
            return None

    def get_join_url(self) -> str:
        """Возвращает URL для вступления в канал/чат."""
        if self._channel_username:
            return f"https://t.me/{self._channel_username}"
        return self._invite_link or ""
