from __future__ import annotations

import re

from django.core.cache import cache


GARBAGE_KEYWORD_CACHE_KEY = "assistant_linking:garbage_keywords:v1"
GARBAGE_RULE_KINDS = ("garbage_keyword", "exclude_keyword")
GARBAGE_MODIFIER = "garbage"
GARBAGE_WARNING_PREFIX = "excluded garbage keyword"


def normalize_garbage_keyword(value: str) -> str:
    text = (value or "").replace(";", "\n").replace(",", "\n")
    terms: list[str] = []
    seen: set[str] = set()
    for term in text.splitlines():
        cleaned = re.sub(r"\s+", " ", term.strip())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(cleaned)
    return "\n".join(terms)


def clear_garbage_keyword_cache():
    cache.delete(GARBAGE_KEYWORD_CACHE_KEY)


def get_garbage_keywords() -> list[str]:
    keywords = cache.get(GARBAGE_KEYWORD_CACHE_KEY)
    if keywords is not None:
        return keywords

    from assistant_core.models import GlobalRule

    rows = (
        GlobalRule.objects.filter(
            active=True,
            approved=True,
            rule_kind__in=GARBAGE_RULE_KINDS,
        )
        .order_by("priority", "title")
        .values_list("rule_text", flat=True)
    )
    keywords = []
    seen = set()
    for rule_text in rows:
        for keyword in normalize_garbage_keyword(rule_text).splitlines():
            key = keyword.casefold()
            if key in seen:
                continue
            seen.add(key)
            keywords.append(keyword)
    cache.set(GARBAGE_KEYWORD_CACHE_KEY, keywords, 300)
    return keywords


def match_garbage_keyword(text: str) -> str:
    haystack = (text or "").casefold()
    for keyword in get_garbage_keywords():
        if keyword.casefold() in haystack:
            return keyword
    return ""
