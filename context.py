"""
Общий контекст приложения: конфиг, репозитории, планировщик.
Инициализируется в main.py, используется хендлерами.
"""

from dataclasses import dataclass, field

from aiogram import Bot

from albums import AlbumBuffer
from channel_checker import ChannelChecker
from config_loader import Config
from conversion_queue import ConversionQueue
from db import Database
from locks import UserLocks
from plans import Plans
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
from request_tracker import RequestTracker
from tasks import JobRunner, TaskRegistry
from tempfiles import TempFiles
from video_converter import VideoConverter


@dataclass
class AppContext:
    config: Config
    plans: Plans
    db: Database
    users: UserRepo
    subs: SubscriptionRepo
    usage: UsageRepo
    settings: SettingsRepo
    channel: ChannelRepo
    payments: PaymentRepo
    support: SupportRepo
    tasks: TaskRegistry
    jobs: JobRunner
    bot: Bot
    channel_checker: ChannelChecker
    converter: VideoConverter
    albums: AlbumBuffer = field(default_factory=AlbumBuffer)
    locks: UserLocks = field(default_factory=UserLocks)
    conversion_queue: ConversionQueue = field(default_factory=ConversionQueue)
    conversion_cache: ConversionCacheRepo | None = None
    tempfiles: TempFiles | None = None
    requests: RequestTracker = field(default_factory=RequestTracker)


# Синглтон, заполняется в main()
ctx: AppContext | None = None


def get_ctx() -> AppContext:
    assert ctx is not None, "AppContext не инициализирован"
    return ctx
