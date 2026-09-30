"""
Общий контекст приложения: конфиг, репозитории, планировщик.
Инициализируется в main.py, используется хендлерами.
"""

from dataclasses import dataclass, field
from typing import Optional

from aiogram import Bot

from config_loader import Config
from db import Database
from plans import Plans
from repositories import (
    ChannelRepo, PaymentRepo, SettingsRepo, SubscriptionRepo, UsageRepo, UserRepo,
)
from tasks import JobRunner, TaskRegistry


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
    tasks: TaskRegistry
    jobs: JobRunner
    bot: Optional[Bot] = None
    channel_checker: object = None      # ChannelChecker, задаётся в main
    converter: object = None            # VideoConverter, задаётся в main


# Синглтон, заполняется в main()
ctx: Optional[AppContext] = None


def get_ctx() -> AppContext:
    assert ctx is not None, "AppContext не инициализирован"
    return ctx
