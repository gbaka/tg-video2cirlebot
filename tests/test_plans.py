"""Тарифы, цены и определение прав."""

from plans import Plan, Plans, PriceOption


def make_free() -> Plan:
    return Plan("free", 50, 60, [360], 360, 24, "fast", 5, 3)


def make_pro() -> Plan:
    return Plan("pro", 200, 60, [360, 480, 640], 480, 20, "medium", 0, 0)


def test_max_size_bytes() -> None:
    assert make_free().max_size_bytes == 50 * 1024 * 1024
    assert Plan("x", 0, 60, [360], 360, 24, "fast").max_size_bytes == 0  # 0 = без лимита


def test_daily_limit_flags() -> None:
    assert not make_free().is_unlimited()
    assert make_pro().is_unlimited()


def test_album_limit_flags() -> None:
    assert make_free().max_album == 3
    assert not make_free().album_unlimited()
    assert make_pro().album_unlimited()


def test_normalize_resolution() -> None:
    free, pro = make_free(), make_pro()
    assert free.normalize_resolution(None) == 360
    assert free.normalize_resolution(640) == 360      # не выше доступного
    assert pro.normalize_resolution(None) == 480
    assert pro.normalize_resolution(640) == 640
    assert pro.normalize_resolution(999) == 640       # ближайшее меньшее
    assert pro.normalize_resolution(400) == 360


def test_resolve_prefers_pro_for_admin() -> None:
    plans = Plans(make_free(), make_pro(), [])
    assert plans.resolve(True, False).code == "pro"
    assert plans.resolve(False, True, "pro").code == "pro"
    assert plans.resolve(False, False).code == "free"
    assert plans.resolve(False, True, "unknown").code == "free"   # неизвестный тариф


def test_price_lookup() -> None:
    price = PriceOption("pro_30d", "pro", 30, 25)
    plans = Plans(make_free(), make_pro(), [price])
    assert plans.price("pro_30d") is price
    assert plans.price("nope") is None
