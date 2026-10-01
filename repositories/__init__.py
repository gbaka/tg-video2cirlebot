"""
Репозитории: доступ к данным SQLite.

Пакет разбит по таблицам; здесь — единая точка импорта, поэтому
`from repositories import UserRepo` работает как раньше.
"""

from repositories.base import LIFETIME_EXPIRES, Base, is_lifetime
from repositories.channel import ChannelRepo
from repositories.payments import PaymentRepo
from repositories.settings import SettingsRepo
from repositories.subscriptions import SubscriptionRepo
from repositories.support import SupportRepo
from repositories.usage import UsageRepo
from repositories.users import UserRepo

__all__ = [
    "LIFETIME_EXPIRES",
    "Base",
    "ChannelRepo",
    "PaymentRepo",
    "SettingsRepo",
    "SubscriptionRepo",
    "SupportRepo",
    "UsageRepo",
    "UserRepo",
    "is_lifetime",
]
