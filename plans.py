"""
Тарифные планы: лимиты, определение прав пользователя.
"""

from dataclasses import dataclass


@dataclass
class Plan:
    """Тариф: набор лимитов и параметров кодирования."""
    code: str
    max_size_mb: int
    max_duration_sec: int
    resolutions: list[int]
    default_resolution: int
    crf: int
    preset: str
    daily_limit: int = 0          # 0 = без ограничения
    max_album: int = 1            # видео в одном сообщении, 0 = без ограничения

    @property
    def max_size_bytes(self) -> int:
        return self.max_size_mb * 1024 * 1024 if self.max_size_mb > 0 else 0

    def is_unlimited(self) -> bool:
        return self.daily_limit <= 0

    def album_unlimited(self) -> bool:
        return self.max_album <= 0

    def normalize_resolution(self, requested: int | None) -> int:
        """Возвращает допустимое разрешение (ближайшее из доступных)."""
        if not requested:
            return self.default_resolution
        if requested in self.resolutions:
            return requested
        # Ближайшее меньшее или максимальное доступное
        lower = [r for r in self.resolutions if r <= requested]
        return max(lower) if lower else self.default_resolution


@dataclass
class PriceOption:
    """Вариант покупки подписки за Telegram Stars."""
    code: str            # 'pro_30d'
    plan: str            # 'pro'
    days: int
    stars: int
    label: str = ""      # отображаемое название (i18n или plain)
    lifetime: bool = False   # бессрочная подписка


class Plans:
    """Реестр тарифов и цен."""

    def __init__(
        self,
        free: Plan,
        pro: Plan,
        prices: list[PriceOption],
    ):
        self._plans = {free.code: free, pro.code: pro}
        self.free = free
        self.pro = pro
        self.prices = prices

    def get(self, code: str) -> Plan:
        return self._plans.get(code, self.free)

    def price(self, code: str) -> PriceOption | None:
        for p in self.prices:
            if p.code == code:
                return p
        return None

    def resolve(
        self,
        is_admin: bool,
        has_active_subscription: bool,
        subscription_plan: str | None = None,
    ) -> Plan:
        """
        Определяет действующий тариф.
        Админы получают Pro-лимиты по умолчанию.
        """
        if is_admin:
            return self.pro
        if has_active_subscription and subscription_plan:
            return self.get(subscription_plan)
        return self.free
