"""
Работа со временем в UTC.

Все метки времени в базе — строки 'YYYY-MM-DD HH:MM:SS' в UTC (так их пишет
SQLite через CURRENT_TIMESTAMP). Функции ниже дают такие же строки, чтобы
сравнения в SQL оставались строковыми и использовали индексы.
"""

from datetime import UTC, datetime, timedelta

#: Формат меток времени в базе
FMT = "%Y-%m-%d %H:%M:%S"


def now() -> str:
    """Текущее время UTC."""
    return datetime.now(UTC).strftime(FMT)


def ago(days: int = 0, hours: int = 0, minutes: int = 0) -> str:
    """Время UTC, отстоящее назад на указанный интервал."""
    return (datetime.now(UTC) - timedelta(days=days, hours=hours, minutes=minutes)).strftime(FMT)


def after(days: int = 0, hours: int = 0) -> str:
    """Время UTC, отстоящее вперёд на указанный интервал."""
    return (datetime.now(UTC) + timedelta(days=days, hours=hours)).strftime(FMT)
