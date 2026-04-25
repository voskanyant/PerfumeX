from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q

from assistant_linking.models import BrandAlias, ProductAlias


AUDIENCE_SYNONYMS = {
    "men": {"men", "man", "male", "homme", "uomo", "pour homme", "him", "m", "муж", "мужской", "мужская", "мужские"},
    "women": {"women", "woman", "female", "femme", "donna", "pour femme", "her", "lady", "w", "жен", "женский", "женская", "женские"},
    "unisex": {"unisex", "унисекс", "уни"},
}

AUDIENCE_DISPLAY_VALUES = {
    "men": {"men", "Men", "Homme", "Pour Homme"},
    "women": {"women", "Woman", "Femme", "Pour Femme"},
    "unisex": {"unisex", "Unisex"},
}

CONCENTRATION_SYNONYMS = {
    "Eau de Parfum": {"edp", "eau de parfum"},
    "Eau de Toilette": {"edt", "eau de toilette"},
    "Eau de Cologne": {"edc", "eau de cologne"},
    "Parfum": {"parfum", "perfume"},
    "Extrait de Parfum": {"extrait", "extrait de parfum"},
}

NOISE_VARIANTS = {
    "tester": {"tester", "test", "тестер"},
    "sample": {"sample", "vial", "пробник"},
    "travel": {"travel", "mini", "мини"},
    "set": {"set", "coffret", "набор"},
}

IDENTITY_MODIFIERS = {
    "intense",
    "eau intense",
    "elixir",
    "absolu",
    "absolute",
    "extreme",
    "forever",
    "love in capri",
    "summer vibes",
    "light blue love",
}


@dataclass
class SmartSearchIntent:
    raw_query: str
    product_terms: list[str] = field(default_factory=list)
    audience: str = ""
    concentration: str = ""
    size_ml: Decimal | None = None
    requested_noise: set[str] = field(default_factory=set)
    requested_modifiers: set[str] = field(default_factory=set)
    brand_alias_terms: list[str] = field(default_factory=list)
    product_alias_terms: list[str] = field(default_factory=list)


def normalize_query(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[\u00a0_/,;:|()\[\]{}]+", " ", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(^|\s){re.escape(phrase)}($|\s)", text))


def _remove_phrase(text: str, phrase: str) -> str:
    return re.sub(rf"(^|\s){re.escape(phrase)}($|\s)", " ", text).strip()


def _extract_size(text: str) -> tuple[Decimal | None, str]:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:ml|мл)\b", text)
    if not match:
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*$", text)
    if not match:
        return None, text
    value = Decimal(match.group(1)).quantize(Decimal("0.01"))
    if value < 5 or value > 1000:
        return None, text
    return value, text.replace(match.group(0), " ")


def parse_smart_query(query: str) -> SmartSearchIntent:
    text = normalize_query(query)
    intent = SmartSearchIntent(raw_query=query)

    for audience, aliases in AUDIENCE_SYNONYMS.items():
        matched = sorted([alias for alias in aliases if _contains_phrase(text, alias)], key=len, reverse=True)
        if matched:
            intent.audience = audience
            for alias in matched:
                text = _remove_phrase(text, alias)
            break

    for concentration, aliases in CONCENTRATION_SYNONYMS.items():
        matched = sorted([alias for alias in aliases if _contains_phrase(text, alias)], key=len, reverse=True)
        if matched:
            intent.concentration = concentration
            for alias in matched:
                text = _remove_phrase(text, alias)
            break

    size, text = _extract_size(text)
    intent.size_ml = size

    for key, aliases in NOISE_VARIANTS.items():
        if any(_contains_phrase(text, alias) for alias in aliases):
            intent.requested_noise.add(key)
            for alias in aliases:
                text = _remove_phrase(text, alias)

    for modifier in sorted(IDENTITY_MODIFIERS, key=len, reverse=True):
        if _contains_phrase(text, modifier):
            intent.requested_modifiers.add(modifier)
            text = _remove_phrase(text, modifier)

    for alias in BrandAlias.objects.filter(active=True).select_related("brand").order_by("supplier_id", "priority", "-normalized_alias"):
        alias_text = normalize_query(alias.normalized_alias or alias.alias_text)
        if alias_text and _contains_phrase(text, alias_text):
            intent.brand_alias_terms.append(alias.brand.name)
            text = _remove_phrase(text, alias_text)

    for alias in ProductAlias.objects.filter(active=True).order_by("supplier_id", "priority", "-alias_text"):
        alias_text = normalize_query(alias.alias_text)
        if alias_text and alias_text in text:
            intent.product_alias_terms.extend(normalize_query(alias.canonical_text).split())
            text = text.replace(alias_text, " ")

    intent.product_terms = [token for token in re.split(r"\s+", text.strip()) if token]
    return intent


def _term_q(term: str) -> Q:
    return (
        Q(name__icontains=term)
        | Q(supplier_sku__icontains=term)
        | Q(brand__icontains=term)
        | Q(size__icontains=term)
        | Q(assistant_parse__product_name_text__icontains=term)
        | Q(assistant_parse__detected_brand_text__icontains=term)
        | Q(assistant_parse__normalized_brand__name__icontains=term)
    )


def _exclude_phrase_q(phrase: str) -> Q:
    return Q(name__icontains=phrase) | Q(assistant_parse__product_name_text__icontains=phrase)


def apply_smart_supplier_search(queryset, query: str):
    intent = parse_smart_query(query)
    if not normalize_query(query):
        return queryset

    terms = intent.brand_alias_terms + intent.product_alias_terms + intent.product_terms
    for term in terms[:8]:
        queryset = queryset.filter(_term_q(term))

    if intent.audience:
        aliases = AUDIENCE_SYNONYMS[intent.audience]
        raw_q = Q()
        for alias in aliases:
            raw_q |= Q(name__icontains=alias)
        queryset = queryset.filter(Q(assistant_parse__supplier_gender_hint__in=AUDIENCE_DISPLAY_VALUES[intent.audience]) | raw_q)

    if intent.concentration:
        aliases = CONCENTRATION_SYNONYMS[intent.concentration]
        raw_q = Q()
        for alias in aliases:
            raw_q |= Q(name__icontains=alias)
        queryset = queryset.filter(Q(assistant_parse__concentration=intent.concentration) | raw_q)

    if intent.size_ml is not None:
        size_int = int(intent.size_ml)
        queryset = queryset.filter(
            Q(assistant_parse__size_ml=intent.size_ml)
            | Q(name__icontains=f"{size_int}ml")
            | Q(name__icontains=f"{size_int} ml")
            | Q(size__icontains=str(size_int))
        )

    for key, aliases in NOISE_VARIANTS.items():
        if key in intent.requested_noise:
            continue
        noise_q = Q(**{f"assistant_parse__is_{key}": True})
        for alias in aliases:
            noise_q |= Q(name__icontains=alias)
        queryset = queryset.exclude(noise_q)

    for modifier in IDENTITY_MODIFIERS:
        if modifier not in intent.requested_modifiers:
            queryset = queryset.exclude(_exclude_phrase_q(modifier))

    return queryset
