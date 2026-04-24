from __future__ import annotations

import re
from collections import Counter

from assistant_linking.services.normalizer import CONCENTRATION_ALIASES, GENDER_ALIASES, MODIFIER_TERMS, normalize_text
from prices.models import SupplierProduct


BLOCKER_STOP_WORDS = {
    "and",
    "de",
    "eau",
    "for",
    "la",
    "le",
    "ml",
    "of",
    "parfum",
    "perfume",
    "pour",
    "spray",
    "the",
    "toilette",
}


def _remove_known_noise(text: str, brand_text: str) -> str:
    cleaned = text
    for needle, _value in CONCENTRATION_ALIASES:
        cleaned = re.sub(rf"(^|\s){re.escape(needle)}($|\s)", " ", cleaned)
    for pattern, _value in GENDER_ALIASES:
        cleaned = re.sub(pattern, " ", cleaned)
    if brand_text:
        cleaned = re.sub(rf"(^|\s){re.escape(normalize_text(brand_text))}($|\s)", " ", cleaned)
    cleaned = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:ml|fl\s*oz|oz)?\b", " ", cleaned)
    cleaned = re.sub(r"\btester\b|\btest\b|\bsample\b|\btravel\b|\bset\b|\bcoffret\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def suggest_product_alias_blockers(product: SupplierProduct, alias_text: str, brand_text: str = "") -> list[str]:
    normalized_alias = normalize_text(alias_text)
    if not normalized_alias:
        return []

    first_alias_word = normalized_alias.split()[0]
    siblings = SupplierProduct.objects.filter(name__icontains=first_alias_word).exclude(pk=product.pk).only("name")[:1000]
    counts: Counter[str] = Counter()
    for sibling in siblings:
        text = normalize_text(sibling.name)
        if normalized_alias not in text:
            continue
        remainder = text.replace(normalized_alias, " ")
        remainder = _remove_known_noise(remainder, brand_text)
        modifier_hits = [term for term in MODIFIER_TERMS if re.search(rf"(^|\s){re.escape(term)}($|\s)", remainder)]
        if modifier_hits:
            counts.update(modifier_hits)
            continue
        tokens = [token for token in remainder.split() if token not in BLOCKER_STOP_WORDS and len(token) > 2]
        counts.update(tokens[:3])

    return [term for term, _count in counts.most_common(8)]
