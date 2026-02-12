from django import template
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
