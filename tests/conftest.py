"""Общие фикстуры: пути, локали, временная БД, репозитории."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import i18n  # noqa: E402
from db import Database  # noqa: E402
from repositories import (  # noqa: E402
    ChannelRepo,
    PaymentRepo,
    SettingsRepo,
    SubscriptionRepo,
    SupportRepo,
    UsageRepo,
    UserRepo,
)


@pytest.fixture(scope="session", autouse=True)
def _locales() -> None:
    """Локали нужны почти всем тестам — грузим один раз на сессию."""
    i18n.load_locales(ROOT / "locales")


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """Пустая инициализированная БД в отдельном каталоге."""
    database = Database(str(tmp_path / "test.db"))
    await database.init()
    yield database


@pytest.fixture
def users(db: Database) -> UserRepo:
    return UserRepo(db)


@pytest.fixture
def subs(db: Database) -> SubscriptionRepo:
    return SubscriptionRepo(db)


@pytest.fixture
def usage(db: Database) -> UsageRepo:
    return UsageRepo(db)


@pytest.fixture
def settings(db: Database) -> SettingsRepo:
    return SettingsRepo(db)


@pytest.fixture
def channel(db: Database) -> ChannelRepo:
    return ChannelRepo(db)


@pytest.fixture
def payments(db: Database) -> PaymentRepo:
    return PaymentRepo(db)


@pytest.fixture
def support(db: Database) -> SupportRepo:
    return SupportRepo(db)
