"""Лимиты тарифов и тексты про них.

Значения берутся из планов и из лимита загрузки Bot API, а не из текста локали:
иначе реклама Pro разошлась бы с реальными лимитами при первом же изменении
config.yaml. Строка добавляется только если тариф действительно отличается.

max_size_mb = 0 означает «без ограничения по размеру»: тогда файл упирается
только в потолок самого Bot API (20 MB в облачном режиме).
"""

from i18n import t
from plans import Plan


def limit_bytes(ctx, plan: Plan) -> int:
    """Эффективный лимит файла в байтах; 0 — ограничения нет."""
    api = getattr(ctx.config, "api", None)
    return api.input_limit_bytes(plan.max_size_bytes) if api else plan.max_size_bytes


def size_text(lang: str, ctx, plan: Plan) -> str:
    """Лимит файла строкой: «15 MB» или «без ограничений»."""
    limit = limit_bytes(ctx, plan)
    if not limit:
        return t(lang, "sub.limit_none")
    return f"{limit // (1024 * 1024)} MB"


def size_limit_key(ctx, plan: Plan) -> str:
    """Чем ограничен файл: тарифом или потолком Bot API.

    Нужно для текста отказа: «лимит вашего тарифа: 20 MB» — неправда, если
    тариф без ограничения, а файл не отдаёт облачный API.
    """
    limit = limit_bytes(ctx, plan)
    if limit and plan.max_size_bytes and limit == plan.max_size_bytes:
        return "conv.too_big"
    return "conv.too_big_api"


def pro_benefits(lang: str, ctx) -> str:
    """Блок «что даёт Pro» для экрана подписки. Пустая строка, если Pro не лучше."""
    free, pro = ctx.plans.free, ctx.plans.pro
    lines: list[str] = []
    if max(pro.resolutions) > max(free.resolutions):
        lines.append(t(lang, "sub.benefit_quality",
                       pro_res=max(pro.resolutions), free_res=max(free.resolutions)))
    free_size, pro_size = limit_bytes(ctx, free), limit_bytes(ctx, pro)
    if not pro_size and free_size:
        lines.append(t(lang, "sub.benefit_size_unlimited",
                       free_size=free_size // (1024 * 1024)))
    elif pro_size > free_size:
        lines.append(t(lang, "sub.benefit_size",
                       pro_size=pro_size // (1024 * 1024),
                       free_size=free_size // (1024 * 1024)))
    if free.is_unlimited() and pro.is_unlimited():
        pass
    elif pro.is_unlimited():
        lines.append(t(lang, "sub.benefit_limit", free_limit=free.daily_limit))
    elif free.daily_limit < pro.daily_limit:
        lines.append(t(lang, "sub.benefit_limit_number",
                       pro_limit=pro.daily_limit, free_limit=free.daily_limit))
    if pro.album_unlimited() and not free.album_unlimited():
        lines.append(t(lang, "sub.benefit_album_unlimited"))
    elif pro.max_album > free.max_album:
        lines.append(t(lang, "sub.benefit_album",
                       pro_album=pro.max_album, free_album=free.max_album))
    if not lines:
        return ""
    return t(lang, "sub.pro_benefits_title") + "\n" + "\n".join(lines)


def tariff_summary(lang: str, ctx) -> str:
    """Короткое сравнение тарифов для справки /help."""
    free, pro = ctx.plans.free, ctx.plans.pro
    free_limit = (t(lang, "sub.limit_none") if free.is_unlimited()
                  else str(free.daily_limit))
    pro_limit = (t(lang, "sub.limit_none") if pro.is_unlimited()
                 else str(pro.daily_limit))
    return t(
        lang, "help.tariffs",
        free_res=max(free.resolutions), free_size=size_text(lang, ctx, free),
        free_limit=free_limit,
        pro_res=max(pro.resolutions), pro_size=size_text(lang, ctx, pro),
        pro_limit=pro_limit,
    )
