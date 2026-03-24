from django import template
from django.utils import timezone
import re

register = template.Library()


def _count_cyrillic(text: str) -> int:
    return len(re.findall(r"[\u0400-\u04FF]", text))


def _fix_mojibake(text: str) -> str:
    if not text:
        return text
    if _count_cyrillic(text):
        return text
    if not re.search(r"[\u00C0-\u00FF]", text):
        return text
    best = text
    best_score = _count_cyrillic(text)
    for encoding in ("cp1251", "utf-8"):
        try:
            candidate = text.encode("latin-1").decode(encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        score = _count_cyrillic(candidate)
        if score > best_score:
            best = candidate
            best_score = score
    return best


@register.filter
def get_attr(obj, attr_name):
    value = getattr(obj, attr_name)
    if isinstance(value, str):
        return _fix_mojibake(value)
    return value


@register.filter
def fix_text(value):
    if isinstance(value, str):
        return _fix_mojibake(value)
    return value


@register.filter
def currency_symbol(value):
    symbols = {
        "USD": "$",
        "RUB": "?",
    }
    return symbols.get(str(value or "").upper(), value or "")


def _short_relative_time(value) -> str:
    if not value:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    now = timezone.localtime(timezone.now())
    dt_local = timezone.localtime(dt)
    total_seconds = int((now - dt_local).total_seconds())
    if total_seconds <= 0:
        return "just now"
    if total_seconds < 60:
        return "just now"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m ago"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h ago"
    if total_seconds < 604800:
        return f"{total_seconds // 86400}d ago"
    if total_seconds < 2592000:
        return f"{total_seconds // 604800}w ago"
    if total_seconds < 31536000:
        return f"{total_seconds // 2592000}mo ago"
    return f"{total_seconds // 31536000}y ago"


@register.filter
def relative_time_short(value):
    return _short_relative_time(value)


def _imported_age_class(value) -> str:
    if not value:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    now = timezone.localtime(timezone.now())
    dt_local = timezone.localtime(dt)
    age_seconds = max(int((now - dt_local).total_seconds()), 0)
    if age_seconds < 3 * 24 * 60 * 60:
        return "age-fresh"
    if age_seconds <= 5 * 24 * 60 * 60:
        return "age-warn"
    return "age-stale"


@register.filter
def imported_age_class(value):
    return _imported_age_class(value)
