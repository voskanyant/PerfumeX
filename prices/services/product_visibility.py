from __future__ import annotations

from django.db.models import Q


DEFAULT_HIDDEN_PRODUCT_FIELDS = ("name", "brand", "supplier_sku")


def normalize_hidden_product_keywords(raw: str) -> str:
    text = (raw or "").replace(";", "\n").replace(",", "\n")
    terms: list[str] = []
    seen: set[str] = set()
    for term in text.splitlines():
        cleaned = term.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(cleaned)
    return "\n".join(terms)


def parse_hidden_product_keywords(raw: str) -> list[str]:
    normalized = normalize_hidden_product_keywords(raw)
    return [term.lower() for term in normalized.splitlines() if term.strip()]


def get_hidden_product_keywords_for_user(user) -> list[str]:
    if not getattr(user, "is_authenticated", False):
        return []
    from prices.models import UserPreference

    prefs = UserPreference.get_for_user(user)
    return parse_hidden_product_keywords(prefs.supplier_exclude_terms or "")


def apply_hidden_product_keywords(
    queryset,
    keywords: list[str],
    *,
    fields: tuple[str, ...] = DEFAULT_HIDDEN_PRODUCT_FIELDS,
):
    if not keywords:
        return queryset
    for keyword in keywords:
        hidden_match = Q()
        for field in fields:
            hidden_match |= Q(**{f"{field}__icontains": keyword})
        queryset = queryset.exclude(hidden_match)
    return queryset
