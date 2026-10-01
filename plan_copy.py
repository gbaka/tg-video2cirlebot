"""Тексты про тарифы: конкретные преимущества Pro, собранные из конфига.

Значения берутся из планов и из лимита загрузки Bot API, а не из текста локали:
иначе реклама Pro разошлась бы с реальными лимитами при первом же изменении
config.yaml. Строка добавляется только если тариф действительно отличается.
"""

from i18n import t
from plans import Plan


def _mb(ctx, plan: Plan) -> int:
    """Эффективный лимит файла: план не может превысить облачный лимит Bot API."""
    return ctx.config.api.input_limit_bytes(plan.max_size_bytes) // (1024 * 1024)


def pro_benefits(lang: str, ctx) -> str:
    """Блок «что даёт Pro» для экрана подписки. Пустая строка, если Pro не лучше."""
    free, pro = ctx.plans.free, ctx.plans.pro
    lines: list[str] = []
    if max(pro.resolutions) > max(free.resolutions):
        lines.append(t(lang, "sub.benefit_quality",
                       pro_res=max(pro.resolutions), free_res=max(free.resolutions)))
    if _mb(ctx, pro) > _mb(ctx, free):
        lines.append(t(lang, "sub.benefit_size",
                       pro_size=_mb(ctx, pro), free_size=_mb(ctx, free)))
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
        free_res=max(free.resolutions), free_size=_mb(ctx, free), free_limit=free_limit,
        pro_res=max(pro.resolutions), pro_size=_mb(ctx, pro), pro_limit=pro_limit,
    )
