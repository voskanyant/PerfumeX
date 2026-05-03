from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from functools import lru_cache
from urllib.parse import urlencode

import regex
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from assistant_linking.models import BrandAlias
from assistant_linking.models import FragranticaProduct
from assistant_linking.models import ParsedSupplierProduct
from assistant_linking.models import ProductAlias
from assistant_linking.models import TITLECASE_APOSTROPHE_SUFFIXES
from assistant_linking.models import TITLECASE_LOWER_WORDS
from assistant_linking.models import strip_leading_fragrantica_brand_name
from assistant_linking.services.parser_rules import get_regex_preprocess_rules
from assistant_linking.utils.text import normalize_alias_value
from assistant_linking.utils.text import normalize_mixed_script_latin_lookalikes
from catalog.models import Brand as CatalogBrand
from catalog.models import Perfume as CatalogPerfume
from catalog.models import Source as CatalogSource
from catalog.models import PerfumeVariant as CatalogPerfumeVariant
from catalog.models import get_or_create_collection
from prices import models
from prices.services.product_filters import (
    parse_exclude_terms,
    resolve_supplier_exclude_terms,
)
from prices.services.product_visibility import apply_hidden_product_keywords


logger = logging.getLogger(__name__)
FRAGRANTICA_REGEX_TIMEOUT_SECONDS = 1.0
FRAGRANTICA_REVIEW_STATUSES = {
    "all",
    "unlinked",
    "linked",
    "ignored",
}
OUR_PRODUCT_CATALOG_TABS = {"products", "brands", "collections", "concentrations"}
COLLECTION_TITLECASE_ACRONYMS = {
    "DNA",
    "VIP",
    "WB",
}
ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$")
TITLE_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:'[A-Za-zÀ-ÖØ-öø-ÿ]+)?")
CATALOGUE_LINKING_STATUSES = {"all", "unlinked", "linked"}
CATALOGUE_LINKING_SUGGESTION_FILTERS = {"all", "with", "without"}
CATALOGUE_LINKING_CONFIDENCE_FILTERS = {"all", "0", "80", "90", "95", "100"}
CATALOG_VARIANT_SEARCH_FIELDS = (
    "perfume__name",
    "perfume__brand__name",
    "perfume__collection_name",
    "perfume__concentration",
    "size_label",
    "packaging",
    "variant_type",
    "sku",
    "ean",
)
FRAGRANTICA_PRODUCT_SEARCH_FIELDS = (
    "brand_name",
    "normalized_brand_name",
    "name",
    "normalized_name",
    "collection_name",
    "audience",
    "source_path",
    "source_url",
    "source_domain",
)
AUDIENCE_NAME_TERMS = {
    "dame",
    "donna",
    "f",
    "female",
    "femme",
    "for her",
    "for him",
    "for man",
    "for men",
    "for woman",
    "for women",
    "her",
    "him",
    "homme",
    "lady",
    "m",
    "male",
    "man",
    "men",
    "pour femme",
    "pour homme",
    "uomo",
    "w",
    "woman",
    "women",
    "wom",
}
AUDIENCE_GROUP_TERMS = {
    "women": {
        "dame",
        "donna",
        "f",
        "female",
        "femme",
        "for her",
        "for woman",
        "for women",
        "her",
        "lady",
        "pour femme",
        "w",
        "woman",
        "women",
        "wom",
    },
    "men": {
        "for him",
        "for man",
        "for men",
        "him",
        "homme",
        "m",
        "male",
        "man",
        "men",
        "pour homme",
        "uomo",
    },
}
AUDIENCE_DISPLAY_SUFFIXES = {
    "men": {
        "for": "for Men",
        "pour": "Pour Homme",
    },
    "women": {
        "for": "for Women",
        "pour": "Pour Femme",
    },
}
FRAGRANCE_CONCENTRATION_TERM_KEYS = {
    "eau de parfum": "eau de parfum",
    "eau de toilette": "eau de toilette",
    "eau de cologne": "eau de cologne",
    "extrait de parfum": "extrait de parfum",
    "edp": "eau de parfum",
    "edt": "eau de toilette",
    "edc": "eau de cologne",
}
FRAGRANCE_CONCENTRATION_NAME_TERMS = set(FRAGRANCE_CONCENTRATION_TERM_KEYS)
FRAGRANTICA_CONCENTRATION_MISMATCH_SCORE_CAP = 88
CATALOGUE_LINKING_DEFAULT_MIN_SCORE = 80


@dataclass(frozen=True)
class CatalogVariantInlineUpdate:
    brand_name: str
    perfume_name: str
    collection_name: str
    concentration: str
    size_ml: Decimal | None
    size_label: str
    is_tester: bool
    packaging: str
    variant_type: str


@dataclass(frozen=True)
class CatalogTabActionResult:
    level: str
    message: str
    tab: str


@dataclass(frozen=True)
class CatalogTabPostActionResult:
    level: str
    message: str
    redirect_url: str


@dataclass(frozen=True)
class CatalogVariantInlineUpdateResult:
    level: str
    message: str


@dataclass(frozen=True)
class CatalogVariantInlineUpdateActionResult:
    level: str
    message: str
    redirect_url: str


@dataclass(frozen=True)
class FragranticaCatalogueLinkResult:
    level: str
    message: str
    redirect_url: str


@dataclass(frozen=True)
class FragranticaMatchCandidate:
    source: FragranticaProduct
    match_type: str
    score: int
    reason: str
    creates_alias: bool = False
    manual_review_reason: str = ""

    @property
    def source_display_name(self) -> str:
        return fragrantica_source_catalogue_name(self.source)

    @property
    def source_label(self) -> str:
        return f"{self.source.brand_name} / {self.source_display_name}"


@dataclass(frozen=True)
class FragranticaPerfumeCandidate:
    perfume: CatalogPerfume
    score: int
    reason: str


@dataclass(frozen=True)
class CatalogueLinkingBulkResult:
    level: str
    message: str
    redirect_url: str


def catalog_search_tokens(query: str) -> list[str]:
    tokens = [token for token in re.split(r"\s+", query.strip()) if token]
    return tokens if len(tokens) > 1 else []


def normalize_our_product_catalog_tab(value: str | None) -> str:
    tab = (value or "products").strip() or "products"
    if tab not in OUR_PRODUCT_CATALOG_TABS:
        return "products"
    return tab


def catalog_variant_token_filter(
    query_token: str,
    *,
    search_fields: tuple[str, ...] = CATALOG_VARIANT_SEARCH_FIELDS,
) -> Q:
    token_filter = Q()
    for field in search_fields:
        token_filter |= Q(**{f"{field}__icontains": query_token})
    return token_filter


def fragrantica_product_token_filter(query_token: str) -> Q:
    token_filter = Q()
    raw_token = (query_token or "").strip()
    token_values = {
        raw_token,
        normalize_alias_value(raw_token).replace("&", "and"),
    }
    for token_value in token_values:
        if not token_value:
            continue
        for field in FRAGRANTICA_PRODUCT_SEARCH_FIELDS:
            token_filter |= Q(**{f"{field}__icontains": token_value})
    if raw_token.isdigit():
        token_filter |= Q(release_year=int(raw_token))
    return token_filter


def fragrantica_product_search_filter(query: str) -> Q:
    query = (query or "").strip()
    if not query:
        return Q()
    search_filter = fragrantica_product_token_filter(query)
    tokens = catalog_search_tokens(query)
    if tokens:
        token_filter = Q()
        for token in tokens:
            token_filter &= fragrantica_product_token_filter(token)
        search_filter |= token_filter
    return search_filter


def build_our_product_catalog_variant_queryset(
    query: str,
    *,
    variant_manager=None,
):
    variant_manager = variant_manager or CatalogPerfumeVariant.objects
    queryset = variant_manager.select_related("perfume", "perfume__brand")
    query = (query or "").strip()
    if query:
        phrase_filter = catalog_variant_token_filter(query)
        token_filter = Q()
        for token in catalog_search_tokens(query):
            token_filter &= catalog_variant_token_filter(token)
        queryset = queryset.filter(phrase_filter | token_filter)
    return queryset.order_by(
        "perfume__brand__name",
        "perfume__name",
        "perfume__concentration",
        "size_ml",
        "packaging",
    )


def build_our_product_catalog_list_context(
    request,
    list_context: dict,
    *,
    brand_manager=None,
    perfume_manager=None,
    variant_manager=None,
) -> dict:
    brand_manager = brand_manager or CatalogBrand.objects
    perfume_manager = perfume_manager or CatalogPerfume.objects
    variant_manager = variant_manager or CatalogPerfumeVariant.objects

    paginator = list_context.get("paginator")
    variants = list_context.get("variants", [])
    total_count = paginator.count if paginator else len(variants)

    collection_rows = (
        perfume_manager.exclude(collection_name="")
        .values("brand_id", "brand__name", "collection_name")
        .annotate(perfume_count=Count("id"))
        .order_by("brand__name", "collection_name")
    )
    concentration_rows = (
        perfume_manager.exclude(concentration="")
        .values("concentration")
        .annotate(perfume_count=Count("id"))
        .order_by("concentration")
    )
    variant_type_rows = (
        variant_manager.exclude(variant_type="")
        .values_list("variant_type", flat=True)
        .distinct()
        .order_by("variant_type")
    )

    visible_variants = list(variants)
    attach_fragrantica_candidates_to_variants(visible_variants)

    return {
        "variants": visible_variants,
        "total_count": total_count,
        "search_query": request.GET.get("q", "").strip(),
        "active_tab": normalize_our_product_catalog_tab(request.GET.get("tab")),
        "brand_rows": brand_manager.annotate(perfume_count=Count("perfumes")).order_by(
            "name"
        ),
        "collection_rows": collection_rows,
        "concentration_rows": concentration_rows,
        "variant_type_rows": variant_type_rows,
    }


def normalized_fragrance_key(value: str) -> str:
    text = normalize_alias_value(value or "").replace("&", "and")
    return re.sub(r"\bet\b", "and", text)


def loose_fragrance_key(value: str) -> str:
    text = normalized_fragrance_key(value)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fragrance_key_variants(value: str) -> set[str]:
    loose_key = loose_fragrance_key(value)
    return {
        key
        for key in {
            normalized_fragrance_key(value),
            loose_key,
            loose_key.replace(" ", ""),
        }
        if key
    }


def _safe_fragrantica_regex_sub(pattern: str, replacement: str, text: str) -> str:
    try:
        return regex.sub(
            pattern,
            replacement,
            text,
            timeout=FRAGRANTICA_REGEX_TIMEOUT_SECONDS,
        )
    except (TimeoutError, regex.error) as exc:
        logger.warning("Fragrantica regex preprocess rule skipped: %s", exc)
        return text


@lru_cache(maxsize=32768)
def _fragrance_match_text_values_cached(
    value: str,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    text = value or ""
    values = {text}
    if regex_preprocess_rules:
        preprocessed = unicodedata.normalize("NFKC", text)
        preprocessed = normalize_mixed_script_latin_lookalikes(preprocessed).lower()
        for pattern, replacement in regex_preprocess_rules:
            preprocessed = _safe_fragrantica_regex_sub(
                pattern,
                replacement,
                preprocessed,
            )
        preprocessed = re.sub(r"\s+", " ", preprocessed).strip()
        if preprocessed:
            values.add(preprocessed)
    return tuple(sorted(values))


def fragrance_match_text_values(
    value: str,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> set[str]:
    return set(_fragrance_match_text_values_cached(value, regex_preprocess_rules))


@lru_cache(maxsize=32768)
def _fragrance_match_key_variants_cached(
    value: str,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    keys: set[str] = set()
    for match_value in _fragrance_match_text_values_cached(
        value,
        regex_preprocess_rules,
    ):
        keys.update(fragrance_key_variants(match_value))
    return tuple(sorted(keys))


@lru_cache(maxsize=32768)
def _fragrance_precise_identity_match_keys_cached(
    value: str,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    keys: set[str] = set()
    for match_value in _fragrance_match_text_values_cached(
        value,
        regex_preprocess_rules,
    ):
        keys.update(fragrance_precise_identity_key_variants(match_value))
    return tuple(sorted(keys))


@lru_cache(maxsize=32768)
def _fragrance_loose_identity_match_keys_cached(
    value: str,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    keys: set[str] = set()
    for match_value in _fragrance_match_text_values_cached(
        value,
        regex_preprocess_rules,
    ):
        keys.update(fragrance_loose_identity_key_variants(match_value))
    return tuple(sorted(keys))


def fragrance_match_key_variants(
    *values: str,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> set[str]:
    keys: set[str] = set()
    for value in values:
        keys.update(
            _fragrance_match_key_variants_cached(
                value,
                regex_preprocess_rules,
            )
        )
    return keys


def fragrance_precise_identity_match_keys(
    *values: str,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> set[str]:
    keys: set[str] = set()
    for value in values:
        keys.update(
            _fragrance_precise_identity_match_keys_cached(
                value,
                regex_preprocess_rules,
            )
        )
    return keys


def fragrance_loose_identity_match_keys(
    *values: str,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> set[str]:
    keys: set[str] = set()
    for value in values:
        keys.update(
            _fragrance_loose_identity_match_keys_cached(
                value,
                regex_preprocess_rules,
            )
        )
    return keys


def fragrance_name_without_concentration(value: str) -> str:
    text = normalized_fragrance_key(value)
    for term in sorted(FRAGRANCE_CONCENTRATION_NAME_TERMS, key=len, reverse=True):
        text = re.sub(rf"(^|\s){re.escape(term)}($|\s)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def display_name_without_concentration(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return ""
    for term in sorted(FRAGRANCE_CONCENTRATION_NAME_TERMS, key=len, reverse=True):
        term_pattern = re.escape(term).replace(r"\ ", r"\s+")
        text = re.sub(
            rf"(?<!\w){term_pattern}(?!\w)",
            " ",
            text,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\s+", " ", text).strip(" -/.,")


def fragrance_concentration_identity_key(value: str) -> str:
    text = normalized_fragrance_key(value)
    return FRAGRANCE_CONCENTRATION_TERM_KEYS.get(text, text)


def fragrance_concentration_keys_from_text(*values: str) -> set[str]:
    keys: set[str] = set()
    terms = sorted(
        FRAGRANCE_CONCENTRATION_TERM_KEYS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for value in values:
        text = normalized_fragrance_key(value)
        if not text:
            continue
        exact_key = FRAGRANCE_CONCENTRATION_TERM_KEYS.get(text)
        if exact_key:
            keys.add(exact_key)
            continue
        for term, canonical_key in terms:
            if re.search(rf"(^|\s){re.escape(term)}($|\s)", text):
                keys.add(canonical_key)
    return keys


def _score_with_fragrantica_concentration_guard(
    score: int,
    reason: str,
    source_names: tuple[str, ...],
    perfume,
) -> tuple[int, str]:
    source_concentration_keys = fragrance_concentration_keys_from_text(*source_names)
    perfume_concentration_key = fragrance_concentration_identity_key(
        getattr(perfume, "concentration", "")
    )
    if (
        source_concentration_keys
        and perfume_concentration_key
        and perfume_concentration_key not in source_concentration_keys
    ):
        return (
            min(score, FRAGRANTICA_CONCENTRATION_MISMATCH_SCORE_CAP),
            "Same brand and scent; concentration differs",
        )
    if (
        source_concentration_keys
        and perfume_concentration_key
        and perfume_concentration_key in source_concentration_keys
        and score >= 98
    ):
        return score, "Exact brand, scent, and concentration match"
    return score, reason


def fragrance_precise_identity_key_variants(value: str) -> set[str]:
    identity_values = {
        value or "",
        fragrance_name_without_concentration(value),
    }
    keys: set[str] = set()
    for identity_value in identity_values:
        keys.update(fragrance_key_variants(identity_value))
    return keys


def fragrance_loose_identity_key_variants(value: str) -> set[str]:
    identity_values = {
        value or "",
        fragrance_name_without_audience(value),
        fragrance_name_without_concentration(value),
        fragrance_name_without_audience_or_concentration(value),
    }
    keys: set[str] = set()
    for identity_value in identity_values:
        keys.update(fragrance_key_variants(identity_value))
    return keys


def _contains_audience_term(name_key: str, term: str) -> bool:
    return bool(re.search(rf"(^|\s){re.escape(term)}($|\s)", name_key))


def audience_group_from_text(*values: str) -> str:
    haystack = normalized_fragrance_key(" ".join(value for value in values if value))
    for group, terms in AUDIENCE_GROUP_TERMS.items():
        if any(_contains_audience_term(haystack, term) for term in terms):
            return group
    return ""


def fragrance_name_without_audience(value: str) -> str:
    text = normalized_fragrance_key(value)
    for term in sorted(AUDIENCE_NAME_TERMS, key=len, reverse=True):
        text = re.sub(rf"(^|\s){re.escape(term)}($|\s)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fragrance_name_without_audience_or_concentration(value: str) -> str:
    text = fragrance_name_without_audience(value)
    for term in sorted(FRAGRANCE_CONCENTRATION_NAME_TERMS, key=len, reverse=True):
        text = re.sub(rf"(^|\s){re.escape(term)}($|\s)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def loose_fragrance_name_without_audience(value: str) -> str:
    return loose_fragrance_key(fragrance_name_without_audience(value))


def loose_fragrance_name_without_audience_or_concentration(value: str) -> str:
    return loose_fragrance_key(fragrance_name_without_audience_or_concentration(value))


def fragrantica_source_is_available_for_perfume(source, perfume) -> bool:
    matched_perfume_id = getattr(source, "matched_perfume_id", None)
    if matched_perfume_id is None and getattr(source, "matched_perfume", None):
        matched_perfume_id = source.matched_perfume.id
    return not matched_perfume_id or matched_perfume_id == perfume.id


def _candidate_sort_key(candidate: FragranticaMatchCandidate):
    return (
        -candidate.score,
        candidate.source.match_status != FragranticaProduct.STATUS_LINKED,
        candidate.source.brand_name,
        fragrantica_source_catalogue_name(candidate.source),
        candidate.source.id,
    )


def add_fragrantica_candidate(
    candidates: dict[int, FragranticaMatchCandidate],
    source,
    *,
    match_type: str,
    score: int,
    reason: str,
    creates_alias: bool,
) -> None:
    existing = candidates.get(source.id)
    candidate = FragranticaMatchCandidate(
        source=source,
        match_type=match_type,
        score=score,
        reason=reason,
        creates_alias=creates_alias,
    )
    if not existing or _candidate_sort_key(candidate) < _candidate_sort_key(existing):
        candidates[source.id] = candidate


def build_fragrantica_candidates_for_perfume(
    perfume,
    sources,
    aliases,
    *,
    limit: int = 3,
) -> list[FragranticaMatchCandidate]:
    brand_key = normalized_fragrance_key(perfume.brand.name)
    name_key = normalized_fragrance_key(perfume.name)
    name_base = fragrance_name_without_audience(perfume.name)
    audience_group = audience_group_from_text(perfume.audience, perfume.name)
    candidates: dict[int, FragranticaMatchCandidate] = {}

    for source in sources:
        if source.normalized_brand_name != brand_key:
            continue
        if not fragrantica_source_is_available_for_perfume(source, perfume):
            continue
        source_names = _fragrantica_source_match_names(source)
        source_audience_group = audience_group_from_text(source.audience, *source_names)
        audience_compatible = (
            not audience_group
            or not source_audience_group
            or audience_group == source_audience_group
        )
        if not audience_compatible:
            continue
        source_name_keys = {normalized_fragrance_key(name) for name in source_names}
        creates_alias = name_key not in source_name_keys
        if name_key in source_name_keys:
            score, reason = _score_with_fragrantica_concentration_guard(
                100,
                "Exact brand and scent match",
                source_names,
                perfume,
            )
            add_fragrantica_candidate(
                candidates,
                source,
                match_type="exact",
                score=score,
                reason=reason,
                creates_alias=False,
            )
            continue

        source_bases = {
            fragrance_name_without_audience(source_name) for source_name in source_names
        }
        source_bases.discard("")
        if (
            name_base
            and source_bases
            and name_base in source_bases
            and audience_compatible
        ):
            score, reason = _score_with_fragrantica_concentration_guard(
                92,
                "Same brand and scent after audience words",
                source_names,
                perfume,
            )
            add_fragrantica_candidate(
                candidates,
                source,
                match_type="name hint",
                score=score,
                reason=reason,
                creates_alias=creates_alias,
            )
            continue

        ratio = max(
            (
                SequenceMatcher(None, name_key, source_key).ratio()
                for source_key in source_name_keys
            ),
            default=0,
        )
        base_ratio = (
            max(
                (
                    SequenceMatcher(None, name_base, source_base).ratio()
                    for source_base in source_bases
                ),
                default=0,
            )
            if name_base
            else 0
        )
        best_ratio = max(ratio, base_ratio)
        if audience_compatible and best_ratio >= 0.82:
            score, reason = _score_with_fragrantica_concentration_guard(
                int(best_ratio * 100),
                "Similar same-brand scent name",
                source_names,
                perfume,
            )
            add_fragrantica_candidate(
                candidates,
                source,
                match_type="fuzzy",
                score=score,
                reason=reason,
                creates_alias=creates_alias,
            )

    for alias in aliases:
        alias_brand_id = getattr(alias, "brand_id", None)
        if alias_brand_id and alias_brand_id != perfume.brand_id:
            continue
        alias_perfume_id = getattr(alias, "perfume_id", None)
        alias_key = normalized_fragrance_key(alias.alias_text)
        canonical_key = normalized_fragrance_key(alias.canonical_text)
        if not alias_key or not canonical_key:
            continue
        if alias_perfume_id and alias_perfume_id != perfume.id:
            continue
        expected_source_keys = set()
        if canonical_key == name_key or alias_perfume_id == perfume.id:
            expected_source_keys.add(alias_key)
        if alias_key == name_key:
            expected_source_keys.add(canonical_key)
        for source in sources:
            if source.normalized_brand_name != brand_key:
                continue
            if not fragrantica_source_is_available_for_perfume(source, perfume):
                continue
            source_names = _fragrantica_source_match_names(source)
            source_name_keys = {
                normalized_fragrance_key(name)
                for name in source_names
            }
            if source_name_keys & expected_source_keys:
                score, reason = _score_with_fragrantica_concentration_guard(
                    96,
                    "Matched by product alias knowledge",
                    source_names,
                    perfume,
                )
                add_fragrantica_candidate(
                    candidates,
                    source,
                    match_type="alias",
                    score=score,
                    reason=reason,
                    creates_alias=name_key not in source_name_keys,
                )

    return sorted(candidates.values(), key=_candidate_sort_key)[:limit]


def attach_fragrantica_candidates_to_variants(
    variants,
    *,
    fragrantica_manager=None,
    product_alias_manager=None,
) -> list:
    variants = list(variants)
    perfumes = {
        variant.perfume_id: variant.perfume
        for variant in variants
        if getattr(variant, "perfume_id", None) and getattr(variant, "perfume", None)
    }
    if not perfumes:
        return variants
    linked_source_map = build_linked_fragrantica_sources_by_perfume_ids(
        perfumes.keys(),
        fragrantica_manager=fragrantica_manager,
    )
    candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
        perfumes.values(),
        fragrantica_manager=fragrantica_manager,
        product_alias_manager=product_alias_manager,
        min_score=0,
        limit=3,
    )
    for variant in variants:
        linked_sources = linked_source_map.get(variant.perfume_id, [])
        variant.fragrantica_linked_sources = linked_sources
        variant.fragrantica_candidates = (
            [] if linked_sources else candidate_map.get(variant.perfume_id, [])
        )
    return variants


def build_linked_fragrantica_sources_by_perfume_ids(
    perfume_ids,
    *,
    fragrantica_manager=None,
) -> dict[int, list]:
    clean_ids = [int(perfume_id) for perfume_id in perfume_ids if perfume_id]
    if not clean_ids:
        return {}
    fragrantica_manager = fragrantica_manager or FragranticaProduct.objects
    linked_sources = (
        fragrantica_manager.filter(
            matched_perfume_id__in=clean_ids,
            match_status=FragranticaProduct.STATUS_LINKED,
        )
        .select_related("matched_perfume", "matched_perfume__brand")
        .order_by("brand_name", "collection_name", "name", "release_year", "id")
    )
    source_map: dict[int, list] = defaultdict(list)
    for source in linked_sources:
        source.catalogue_display_name = fragrantica_source_catalogue_name(source)
        source_map[source.matched_perfume_id].append(source)
    return source_map


def normalize_fragrantica_review_brand_id(value: str | None) -> str:
    brand_id = value or ""
    if brand_id and not str(brand_id).isdigit():
        return ""
    return str(brand_id)


def build_fragrantica_review_perfume_queryset(
    brand_id: str,
    search_query: str,
    *,
    perfume_manager=None,
):
    perfume_manager = perfume_manager or CatalogPerfume.objects
    perfumes = perfume_manager.select_related("brand").annotate(
        variant_count=Count("variants", distinct=True),
        linked_supplier_count=Count("supplier_products", distinct=True),
    )
    if brand_id:
        perfumes = perfumes.filter(brand_id=brand_id)
    if search_query:
        perfumes = perfumes.filter(
            Q(name__icontains=search_query)
            | Q(brand__name__icontains=search_query)
            | Q(collection_name__icontains=search_query)
            | Q(concentration__icontains=search_query)
        )
    return perfumes.order_by("brand__name", "collection_name", "name")


def build_parsed_supplier_evidence_by_name(
    brand_ids: set[int],
    *,
    parsed_product_manager=None,
) -> dict:
    parsed_product_manager = parsed_product_manager or ParsedSupplierProduct.objects
    parsed_rows = parsed_product_manager.select_related(
        "normalized_brand",
        "supplier_product",
        "supplier_product__supplier",
    ).filter(normalized_brand_id__in=brand_ids)
    evidence_by_name = defaultdict(list)
    for parsed in parsed_rows.order_by(
        "normalized_brand__name",
        "collection_name",
        "product_name_text",
        "supplier_product__supplier__name",
    ):
        key = (
            parsed.normalized_brand_id,
            catalogue_review_name_key(parsed.product_name_text),
        )
        if key[1]:
            evidence_by_name[key].append(parsed)
    return evidence_by_name


def build_fragrantica_product_review_context(
    request,
    *,
    fragrantica_manager=None,
    perfume_manager=None,
    paginator_class=Paginator,
    page_size: int = 50,
) -> dict:
    fragrantica_manager = fragrantica_manager or FragranticaProduct.objects
    selected_brand = (request.GET.get("brand") or "").strip()
    search_query = request.GET.get("q", "").strip()
    status_filter = normalize_fragrantica_review_status(
        request.GET.get("status", "all")
    )

    base_queryset = fragrantica_manager.select_related(
        "collection",
        "matched_perfume",
        "matched_perfume__brand",
    )
    brands = (
        base_queryset.values("brand_name")
        .annotate(perfume_count=Count("id"))
        .order_by("brand_name")
    )

    filtered_queryset = base_queryset
    if selected_brand:
        filtered_queryset = filtered_queryset.filter(brand_name__iexact=selected_brand)
    if search_query:
        filtered_queryset = filtered_queryset.filter(
            fragrantica_product_search_filter(search_query)
        )

    status_counts = dict(
        filtered_queryset.values("match_status")
        .annotate(count=Count("id"))
        .values_list("match_status", "count")
    )
    if status_filter != "all":
        filtered_queryset = filtered_queryset.filter(match_status=status_filter)

    total_count = base_queryset.count()
    filtered_count = filtered_queryset.count()
    queryset = filtered_queryset.order_by(
        "brand_name",
        "collection_name",
        "name",
        "audience",
        "release_year",
        "id",
    )
    paginator = paginator_class(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    source_rows = list(page_obj.object_list)
    candidate_choices = build_fragrantica_candidate_choices(
        source_rows,
        perfume_manager=perfume_manager,
    )
    rows = []
    for row in source_rows:
        choices = candidate_choices.get(_fragrantica_source_candidate_key(row), [])
        candidate = row.matched_perfume or (choices[0].perfume if choices else None)
        rows.append(
            {
                "source": row,
                "display_name": fragrantica_source_catalogue_name(row),
                "candidate": candidate,
                "candidate_choices": choices,
            }
        )
    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)

    return {
        "brands": brands,
        "selected_brand": selected_brand,
        "search_query": search_query,
        "status_filter": status_filter,
        "status_counts": status_counts,
        "total_count": total_count,
        "filtered_count": filtered_count,
        "rows": rows,
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": paginator.num_pages > 1,
        "query_string": query_without_page.urlencode(),
    }


def fragrantica_identity_key(brand_name: str, perfume_name: str) -> tuple[str, str]:
    return (
        normalize_alias_value(brand_name or "").replace("&", "and"),
        normalize_alias_value(perfume_name or "").replace("&", "and"),
    )


def fragrantica_source_catalogue_name(source) -> str:
    return strip_leading_fragrantica_brand_name(
        getattr(source, "brand_name", ""),
        getattr(source, "name", ""),
    )


def _fragrantica_source_match_names(source) -> tuple[str, ...]:
    display_name = fragrantica_source_catalogue_name(source)
    names = [display_name]
    collection_stripped = strip_leading_fragrantica_brand_name(
        getattr(source, "collection_name", ""),
        display_name,
    )
    if collection_stripped and collection_stripped != display_name:
        names.append(collection_stripped)
    return tuple(dict.fromkeys(name for name in names if name))


def _build_fragrantica_brand_id_map(
    perfumes,
    brand_alias_manager=None,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> dict[str, set[int]]:
    brand_key_to_ids: dict[str, set[int]] = defaultdict(set)
    brand_ids = set()
    for perfume in perfumes:
        brand = getattr(perfume, "brand", None)
        if not brand:
            continue
        brand_ids.add(perfume.brand_id)
        for key in fragrance_match_key_variants(
            brand.name,
            regex_preprocess_rules=regex_preprocess_rules,
        ):
            brand_key_to_ids[key].add(perfume.brand_id)

    brand_alias_manager = brand_alias_manager or BrandAlias.objects
    if brand_ids:
        alias_queryset = brand_alias_manager.filter(active=True, brand_id__in=brand_ids)
    else:
        alias_queryset = brand_alias_manager.none()
    for alias in alias_queryset:
        for key in fragrance_match_key_variants(
            alias.normalized_alias or alias.alias_text,
            regex_preprocess_rules=regex_preprocess_rules,
        ):
            brand_key_to_ids[key].add(alias.brand_id)
        for key in fragrance_match_key_variants(
            alias.alias_text,
            regex_preprocess_rules=regex_preprocess_rules,
        ):
            brand_key_to_ids[key].add(alias.brand_id)
    return brand_key_to_ids


def _source_brand_ids(
    source,
    brand_key_to_ids: dict[str, set[int]],
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> set[int]:
    keys = fragrance_match_key_variants(
        source.brand_name,
        getattr(source, "normalized_brand_name", ""),
        regex_preprocess_rules=regex_preprocess_rules,
    )
    brand_ids = set()
    for key in keys:
        brand_ids.update(brand_key_to_ids.get(key, set()))
    return brand_ids


def _product_alias_supports_fragrantica_source(
    source,
    perfume,
    alias,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> bool:
    alias_brand_id = getattr(alias, "brand_id", None)
    if alias_brand_id and alias_brand_id != perfume.brand_id:
        return False
    alias_perfume_id = getattr(alias, "perfume_id", None)
    if alias_perfume_id and alias_perfume_id != perfume.id:
        return False

    source_keys = fragrance_loose_identity_match_keys(
        *_fragrantica_source_match_names(source),
        regex_preprocess_rules=regex_preprocess_rules,
    )
    alias_keys = fragrance_loose_identity_match_keys(
        alias.alias_text,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    canonical_keys = fragrance_loose_identity_match_keys(
        alias.canonical_text,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    perfume_keys = fragrance_loose_identity_match_keys(
        perfume.name,
        regex_preprocess_rules=regex_preprocess_rules,
    )

    if alias_perfume_id == perfume.id:
        return bool(source_keys & (alias_keys | canonical_keys))
    return bool(
        (source_keys & alias_keys and perfume_keys & canonical_keys)
        or (source_keys & canonical_keys and perfume_keys & alias_keys)
    )


def _fragrantica_perfume_candidate_score(
    source,
    perfume,
    aliases,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> tuple[int, str]:
    source_names = _fragrantica_source_match_names(source)
    source_audience_group = audience_group_from_text(source.audience, *source_names)
    perfume_audience_group = audience_group_from_text(perfume.audience, perfume.name)
    audience_compatible = (
        not source_audience_group
        or not perfume_audience_group
        or source_audience_group == perfume_audience_group
    )
    if not audience_compatible:
        return 0, ""

    source_precise_name_keys = fragrance_precise_identity_match_keys(
        *source_names,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    perfume_precise_name_keys = fragrance_precise_identity_match_keys(
        perfume.name,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    if source_precise_name_keys & perfume_precise_name_keys:
        return _score_with_fragrantica_concentration_guard(
            100,
            "Exact brand and scent identity match",
            source_names,
            perfume,
        )

    source_name_keys = fragrance_loose_identity_match_keys(
        *source_names,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    perfume_name_keys = fragrance_loose_identity_match_keys(
        perfume.name,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    if source_name_keys & perfume_name_keys:
        return _score_with_fragrantica_concentration_guard(
            98,
            "Exact brand and scent identity match",
            source_names,
            perfume,
        )

    for alias in aliases:
        if _product_alias_supports_fragrantica_source(
            source,
            perfume,
            alias,
            regex_preprocess_rules=regex_preprocess_rules,
        ):
            return _score_with_fragrantica_concentration_guard(
                96,
                "Matched by product alias knowledge",
                source_names,
                perfume,
            )

    source_base_values = [
        fragrance_name_without_audience(name) for name in source_names
    ]
    source_bases = {value for value in source_base_values if value}
    perfume_base = fragrance_name_without_audience(perfume.name)
    source_loose_bases = {
        value
        for value in (
            loose_fragrance_name_without_audience(name) for name in source_names
        )
        if value
    }
    perfume_loose_base = loose_fragrance_name_without_audience(perfume.name)
    source_identity_bases = {
        value
        for value in (
            fragrance_name_without_audience_or_concentration(name)
            for name in source_names
        )
        if value
    }
    perfume_identity_base = fragrance_name_without_audience_or_concentration(
        perfume.name
    )
    source_loose_identity_bases = {
        value
        for value in (
            loose_fragrance_name_without_audience_or_concentration(name)
            for name in source_names
        )
        if value
    }
    perfume_loose_identity_base = (
        loose_fragrance_name_without_audience_or_concentration(perfume.name)
    )
    if audience_compatible and source_bases and perfume_base:
        if perfume_base in source_bases:
            return _score_with_fragrantica_concentration_guard(
                94,
                "Same brand and scent after audience words",
                source_names,
                perfume,
            )
        if (
            source_loose_bases
            and perfume_loose_base
            and perfume_loose_base in source_loose_bases
        ):
            return _score_with_fragrantica_concentration_guard(
                93,
                "Same brand and scent after punctuation and audience words",
                source_names,
                perfume,
            )
        if source_identity_bases and perfume_identity_base:
            if perfume_identity_base in source_identity_bases:
                return _score_with_fragrantica_concentration_guard(
                    95,
                    "Same brand and scent after concentration words",
                    source_names,
                    perfume,
                )
            if (
                source_loose_identity_bases
                and perfume_loose_identity_base
                and perfume_loose_identity_base in source_loose_identity_bases
            ):
                return _score_with_fragrantica_concentration_guard(
                    94,
                    "Same brand and scent after punctuation and concentration words",
                    source_names,
                    perfume,
                )

    ratios = [
        max(
            SequenceMatcher(
                None,
                normalized_fragrance_key(source_name),
                normalized_fragrance_key(perfume.name),
            ).ratio()
            for source_name in source_names
        ),
        max(
            SequenceMatcher(
                None,
                loose_fragrance_key(source_name),
                loose_fragrance_key(perfume.name),
            ).ratio()
            for source_name in source_names
        ),
    ]
    if source_bases and perfume_base:
        ratios.append(
            max(
                SequenceMatcher(None, source_base, perfume_base).ratio()
                for source_base in source_bases
            )
        )
    if source_loose_bases and perfume_loose_base:
        ratios.append(
            max(
                SequenceMatcher(None, source_loose_base, perfume_loose_base).ratio()
                for source_loose_base in source_loose_bases
            )
        )
    if source_identity_bases and perfume_identity_base:
        ratios.append(
            max(
                SequenceMatcher(
                    None,
                    source_identity_base,
                    perfume_identity_base,
                ).ratio()
                for source_identity_base in source_identity_bases
            )
        )
    if source_loose_identity_bases and perfume_loose_identity_base:
        ratios.append(
            max(
                SequenceMatcher(
                    None,
                    source_loose_identity_base,
                    perfume_loose_identity_base,
                ).ratio()
                for source_loose_identity_base in source_loose_identity_bases
            )
        )
    best_ratio = max(ratios)
    if audience_compatible and best_ratio >= 0.82:
        return _score_with_fragrantica_concentration_guard(
            int(best_ratio * 100),
            "Similar same-brand scent name",
            source_names,
            perfume,
        )
    return 0, ""


def _fragrantica_candidate_sort_key(item):
    source, perfume, score, _reason = item
    audience_group = audience_group_from_text(
        source.audience,
        *_fragrantica_source_match_names(source),
    )
    perfume_audience_group = audience_group_from_text(perfume.audience, perfume.name)
    audience_mismatch = bool(
        audience_group
        and perfume_audience_group
        and audience_group != perfume_audience_group
    )
    return (
        -score,
        audience_mismatch,
        perfume.brand.name,
        perfume.name,
        perfume.id,
    )


def _fragrantica_source_candidate_key(source):
    source_id = getattr(source, "id", None)
    if source_id is not None:
        return source_id
    return fragrantica_identity_key(
        source.brand_name,
        fragrantica_source_catalogue_name(source),
    )


def build_fragrantica_candidate_choices(
    fragrantica_rows,
    *,
    perfume_manager=None,
    brand_alias_manager=None,
    product_alias_manager=None,
    limit: int = 4,
) -> dict:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    product_alias_manager = product_alias_manager or ProductAlias.objects
    fragrantica_rows = list(fragrantica_rows)
    if not fragrantica_rows:
        return {}
    candidates = perfume_manager.select_related("brand").filter(name__isnull=False)
    candidates = candidates.filter(brand__name__isnull=False)
    perfumes = list(candidates)
    if not perfumes:
        return {}
    regex_preprocess_rules = get_regex_preprocess_rules()
    brand_key_to_ids = _build_fragrantica_brand_id_map(
        perfumes,
        brand_alias_manager=brand_alias_manager,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    brand_ids = {perfume.brand_id for perfume in perfumes if perfume.brand_id}
    aliases = list(
        product_alias_manager.filter(active=True)
        .filter(Q(brand_id__in=brand_ids) | Q(brand__isnull=True))
        .order_by("priority", "alias_text")
    )
    aliases_by_brand_id: dict[int | None, list[ProductAlias]] = defaultdict(list)
    for alias in aliases:
        aliases_by_brand_id[getattr(alias, "brand_id", None)].append(alias)

    candidate_choices: dict[object, list[FragranticaPerfumeCandidate]] = {}
    for source in fragrantica_rows:
        source_brand_ids = _source_brand_ids(
            source,
            brand_key_to_ids,
            regex_preprocess_rules=regex_preprocess_rules,
        )
        if not source_brand_ids:
            continue
        scored_candidates = []
        for perfume in perfumes:
            if perfume.brand_id not in source_brand_ids:
                continue
            if not fragrantica_source_is_available_for_perfume(source, perfume):
                continue
            candidate_aliases = aliases_by_brand_id.get(
                perfume.brand_id, []
            ) + aliases_by_brand_id.get(None, [])
            score, reason = _fragrantica_perfume_candidate_score(
                source,
                perfume,
                candidate_aliases,
                regex_preprocess_rules=regex_preprocess_rules,
            )
            if score:
                scored_candidates.append((source, perfume, score, reason))
        if not scored_candidates:
            continue
        candidate_choices[_fragrantica_source_candidate_key(source)] = [
            FragranticaPerfumeCandidate(
                perfume=perfume,
                score=score,
                reason=reason,
            )
            for _source, perfume, score, reason in sorted(
                scored_candidates,
                key=_fragrantica_candidate_sort_key,
            )[:limit]
        ]
    return candidate_choices


def normalize_catalogue_linking_status(value: str | None) -> str:
    status = (value or "all").strip() or "all"
    if status not in CATALOGUE_LINKING_STATUSES:
        return "all"
    return status


def normalize_catalogue_linking_suggestion_filter(value: str | None) -> str:
    suggestion_filter = (value or "all").strip() or "all"
    if suggestion_filter not in CATALOGUE_LINKING_SUGGESTION_FILTERS:
        return "all"
    return suggestion_filter


def normalize_catalogue_linking_confidence_filter(value: str | None) -> str:
    confidence_filter = (value or "all").strip() or "all"
    if confidence_filter not in CATALOGUE_LINKING_CONFIDENCE_FILTERS:
        return "all"
    return confidence_filter


def normalize_catalogue_linking_min_score(value: str | None) -> int:
    try:
        score = int(value or CATALOGUE_LINKING_DEFAULT_MIN_SCORE)
    except (TypeError, ValueError):
        return CATALOGUE_LINKING_DEFAULT_MIN_SCORE
    return min(max(score, 0), 100)


def _catalogue_linking_match_type(score: int, reason: str) -> str:
    reason_key = (reason or "").lower()
    if "alias" in reason_key:
        return "alias"
    if score >= 98:
        return "exact"
    if score >= 92:
        return "name hint"
    return "fuzzy"


def _catalogue_linking_brand_keys_for_perfumes(
    perfumes,
    *,
    brand_alias_manager=None,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> set[str]:
    brand_ids = {
        perfume.brand_id for perfume in perfumes if getattr(perfume, "brand_id", None)
    }
    brand_keys = set()
    for perfume in perfumes:
        brand = getattr(perfume, "brand", None)
        if not brand:
            continue
        brand_keys.update(
            fragrance_match_key_variants(
                brand.name,
                regex_preprocess_rules=regex_preprocess_rules,
            )
        )
    brand_alias_manager = brand_alias_manager or BrandAlias.objects
    if brand_ids:
        aliases = brand_alias_manager.filter(active=True, brand_id__in=brand_ids)
    else:
        aliases = brand_alias_manager.none()
    for alias in aliases:
        brand_keys.update(
            fragrance_match_key_variants(
                alias.normalized_alias or alias.alias_text,
                alias.alias_text,
                regex_preprocess_rules=regex_preprocess_rules,
            )
        )
    return {key for key in brand_keys if key}


def build_catalogue_fragrantica_candidates_for_perfumes(
    perfumes,
    *,
    fragrantica_manager=None,
    brand_alias_manager=None,
    product_alias_manager=None,
    limit: int = 5,
    min_score: int = 0,
) -> dict[int, list[FragranticaMatchCandidate]]:
    perfumes = list(perfumes)
    if not perfumes:
        return {}

    fragrantica_manager = fragrantica_manager or FragranticaProduct.objects
    product_alias_manager = product_alias_manager or ProductAlias.objects
    regex_preprocess_rules = get_regex_preprocess_rules()
    brand_key_to_ids = _build_fragrantica_brand_id_map(
        perfumes,
        brand_alias_manager=brand_alias_manager,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    brand_keys = _catalogue_linking_brand_keys_for_perfumes(
        perfumes,
        brand_alias_manager=brand_alias_manager,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    if not brand_keys:
        return {perfume.id: [] for perfume in perfumes}

    source_rows = list(
        fragrantica_manager.select_related("matched_perfume", "collection")
        .filter(normalized_brand_name__in=brand_keys)
        .exclude(match_status=FragranticaProduct.STATUS_IGNORED)
        .order_by("match_status", "brand_name", "collection_name", "name", "id")
    )
    brand_ids = {perfume.brand_id for perfume in perfumes if perfume.brand_id}
    aliases = list(
        product_alias_manager.filter(active=True)
        .filter(Q(brand_id__in=brand_ids) | Q(brand__isnull=True))
        .order_by("priority", "alias_text")
    )
    aliases_by_brand_id: dict[int | None, list[ProductAlias]] = defaultdict(list)
    for alias in aliases:
        aliases_by_brand_id[getattr(alias, "brand_id", None)].append(alias)

    candidates_by_perfume: dict[int, dict[int, FragranticaMatchCandidate]] = {
        perfume.id: {} for perfume in perfumes
    }
    for source in source_rows:
        source_brand_ids = _source_brand_ids(
            source,
            brand_key_to_ids,
            regex_preprocess_rules=regex_preprocess_rules,
        )
        if not source_brand_ids:
            continue
        for perfume in perfumes:
            if perfume.brand_id not in source_brand_ids:
                continue
            if not fragrantica_source_is_available_for_perfume(source, perfume):
                continue
            candidate_aliases = aliases_by_brand_id.get(
                perfume.brand_id, []
            ) + aliases_by_brand_id.get(None, [])
            score, reason = _fragrantica_perfume_candidate_score(
                source,
                perfume,
                candidate_aliases,
                regex_preprocess_rules=regex_preprocess_rules,
            )
            if score < min_score:
                continue
            add_fragrantica_candidate(
                candidates_by_perfume[perfume.id],
                source,
                match_type=_catalogue_linking_match_type(score, reason),
                score=score,
                reason=reason,
                creates_alias=(
                    normalized_fragrance_key(fragrantica_source_catalogue_name(source))
                    != normalized_fragrance_key(perfume.name)
                ),
            )

    _mark_shared_fragrantica_source_conflicts(perfumes, candidates_by_perfume)

    return {
        perfume_id: sorted(candidates.values(), key=_candidate_sort_key)[:limit]
        for perfume_id, candidates in candidates_by_perfume.items()
    }


def _mark_shared_fragrantica_source_conflicts(
    perfumes,
    candidates_by_perfume: dict[int, dict[int, FragranticaMatchCandidate]],
) -> None:
    top_by_source_score: dict[
        tuple[int, int],
        list[tuple[CatalogPerfume, FragranticaMatchCandidate]],
    ] = defaultdict(list)
    for perfume in perfumes:
        candidates = sorted(
            candidates_by_perfume.get(perfume.id, {}).values(),
            key=_candidate_sort_key,
        )
        if not candidates:
            continue
        top_candidate = candidates[0]
        if top_candidate.source.match_status == FragranticaProduct.STATUS_LINKED:
            continue
        top_by_source_score[(top_candidate.source.id, top_candidate.score)].append(
            (perfume, top_candidate)
        )

    for (_source_id, _score), matches in top_by_source_score.items():
        if len(matches) < 2:
            continue
        labels_by_perfume_id = {
            perfume.id: catalogue_linking_perfume_label(perfume)
            for perfume, _candidate in matches
        }
        for perfume, candidate in matches:
            competing_labels = [
                label
                for perfume_id, label in labels_by_perfume_id.items()
                if perfume_id != perfume.id
            ]
            reason = (
                "Manual review: same Fragrantica row is an equal top match for "
                + "; ".join(competing_labels)
            )
            current = candidates_by_perfume.get(perfume.id, {}).get(
                candidate.source.id
            )
            if not current:
                continue
            candidates_by_perfume[perfume.id][candidate.source.id] = replace(
                current,
                manual_review_reason=reason,
            )


def catalogue_linking_perfume_label(perfume) -> str:
    parts = [
        perfume.brand.name,
        normalize_catalogue_perfume_name(perfume.name),
        perfume.concentration,
    ]
    return " / ".join(part for part in parts if part)


def catalogue_linking_source_label(source) -> str:
    parts = [
        source.brand_name,
        fragrantica_source_catalogue_name(source),
        source.audience,
        str(source.release_year) if source.release_year else "",
    ]
    return " / ".join(part for part in parts if part)


def serialize_catalogue_linking_source(source) -> dict:
    return {
        "source_id": source.id,
        "label": catalogue_linking_source_label(source),
        "brand": source.brand_name,
        "name": fragrantica_source_catalogue_name(source),
        "collection": source.collection_name,
        "audience": source.audience,
        "release_year": source.release_year,
        "match_status": source.match_status,
        "source_href": source.source_href,
        "review_url": (
            f"{reverse('prices:fragrantica_product_review')}?"
            f"{urlencode({'q': fragrantica_source_catalogue_name(source)})}"
        ),
    }


def serialize_catalogue_linking_candidate(candidate: FragranticaMatchCandidate) -> dict:
    source = candidate.source
    serialized = serialize_catalogue_linking_source(source)
    serialized.update(
        {
            "score": candidate.score,
            "reason": candidate.reason,
            "match_type": candidate.match_type,
            "creates_alias": candidate.creates_alias,
            "manual_review_reason": candidate.manual_review_reason,
            "link_url": reverse("prices:fragrantica_product_link", args=[source.pk]),
        }
    )
    return serialized


def build_catalogue_linking_perfume_queryset(
    request,
    *,
    perfume_manager=None,
):
    perfume_manager = perfume_manager or CatalogPerfume.objects
    queryset = perfume_manager.select_related("brand", "collection").annotate(
        variant_count=Count("variants", distinct=True),
        linked_fragrantica_count=Count(
            "fragrantica_products",
            filter=Q(
                fragrantica_products__match_status=FragranticaProduct.STATUS_LINKED
            ),
            distinct=True,
        ),
    )
    selected_brand = normalize_fragrantica_review_brand_id(
        request.GET.get("brand") or ""
    )
    search_query = request.GET.get("q", "").strip()
    status_filter = normalize_catalogue_linking_status(request.GET.get("status"))

    if selected_brand:
        queryset = queryset.filter(brand_id=selected_brand)
    if search_query:
        phrase_filter = (
            Q(brand__name__icontains=search_query)
            | Q(name__icontains=search_query)
            | Q(collection_name__icontains=search_query)
            | Q(concentration__icontains=search_query)
            | Q(audience__icontains=search_query)
        )
        token_filter = Q()
        for token in catalog_search_tokens(search_query):
            token_filter &= (
                Q(brand__name__icontains=token)
                | Q(name__icontains=token)
                | Q(collection_name__icontains=token)
                | Q(concentration__icontains=token)
                | Q(audience__icontains=token)
            )
        queryset = queryset.filter(phrase_filter | token_filter)
    if status_filter == "linked":
        queryset = queryset.filter(linked_fragrantica_count__gt=0)
    elif status_filter == "unlinked":
        queryset = queryset.filter(linked_fragrantica_count=0)
    return queryset.order_by("brand__name", "collection_name", "name", "concentration")


def build_catalogue_linking_rows(perfumes, *, min_score: int) -> list[dict]:
    perfumes = list(perfumes)
    linked_source_map = build_linked_fragrantica_sources_by_perfume_ids(
        [perfume.id for perfume in perfumes]
    )
    candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
        perfumes,
        min_score=min_score,
        limit=5,
    )
    rows = []
    for perfume in perfumes:
        linked_sources = linked_source_map.get(perfume.id, [])
        candidates = [] if linked_sources else candidate_map.get(perfume.id, [])
        top_candidate = candidates[0] if candidates else None
        rows.append(
            {
                "perfume": perfume,
                "label": catalogue_linking_perfume_label(perfume),
                "linked_sources": linked_sources,
                "top_linked_source": linked_sources[0] if linked_sources else None,
                "candidates": candidates,
                "top_candidate": top_candidate,
                "ready_for_bulk": bool(
                    top_candidate
                    and top_candidate.score >= min_score
                    and not top_candidate.manual_review_reason
                    and top_candidate.source.match_status
                    != FragranticaProduct.STATUS_LINKED
                ),
            }
        )
    return rows


def filter_catalogue_linking_rows_by_suggestion(
    rows: list[dict],
    suggestion_filter: str,
) -> list[dict]:
    if suggestion_filter == "with":
        return [row for row in rows if row["top_candidate"]]
    if suggestion_filter == "without":
        return [row for row in rows if not row["top_candidate"]]
    return rows


def filter_catalogue_linking_rows_by_confidence(
    rows: list[dict],
    confidence_filter: str,
) -> list[dict]:
    if confidence_filter == "all":
        return rows
    min_score = normalize_catalogue_linking_min_score(confidence_filter)
    return [
        row
        for row in rows
        if row["top_candidate"] and row["top_candidate"].score >= min_score
    ]


def _catalogue_linking_sequence_count(sequence) -> int:
    count = getattr(sequence, "count", None)
    if callable(count) and not isinstance(sequence, (list, tuple)):
        return count()
    return len(sequence)


def _catalogue_linking_sequence_slice(sequence, start: int, stop: int):
    return sequence[start:stop]


def _build_catalogue_linking_filtered_page_rows(
    sequence,
    *,
    page_number: int,
    page_size: int,
    min_score: int,
    confidence_filter: str,
    suggestion_filter: str,
) -> list[dict]:
    target_start = max(page_number - 1, 0) * page_size
    target_stop = target_start + page_size
    matched_seen = 0
    page_rows: list[dict] = []
    total_count = _catalogue_linking_sequence_count(sequence)
    scan_size = max(page_size * 2, page_size)

    for start in range(0, total_count, scan_size):
        perfumes = list(
            _catalogue_linking_sequence_slice(sequence, start, start + scan_size)
        )
        visible_rows = build_catalogue_linking_rows(perfumes, min_score=min_score)
        filtered_rows = filter_catalogue_linking_rows_by_confidence(
            visible_rows,
            confidence_filter,
        )
        filtered_rows = filter_catalogue_linking_rows_by_suggestion(
            filtered_rows,
            suggestion_filter,
        )
        for row in filtered_rows:
            if matched_seen >= target_start and matched_seen < target_stop:
                page_rows.append(row)
            matched_seen += 1
            if matched_seen >= target_stop:
                return page_rows
    return page_rows


def build_catalogue_linking_context(
    request,
    list_context: dict,
    *,
    brand_manager=None,
) -> dict:
    brand_manager = brand_manager or CatalogBrand.objects
    min_score = normalize_catalogue_linking_min_score(request.GET.get("min_score"))
    confidence_filter = normalize_catalogue_linking_confidence_filter(
        request.GET.get("confidence")
    )
    if confidence_filter != "all":
        min_score = normalize_catalogue_linking_min_score(confidence_filter)
    suggestion_filter = normalize_catalogue_linking_suggestion_filter(
        request.GET.get("suggestions")
    )
    perfumes = list(list_context.get("perfumes", []))
    row_filter_active = suggestion_filter != "all" or confidence_filter != "all"
    paginator = list_context.get("paginator")
    page_obj = list_context.get("page_obj")
    if row_filter_active and paginator and page_obj:
        visible_rows = []
        visible_count = paginator.count
        rows = _build_catalogue_linking_filtered_page_rows(
            paginator.object_list,
            page_number=page_obj.number,
            page_size=page_obj.paginator.per_page,
            min_score=min_score,
            confidence_filter=confidence_filter,
            suggestion_filter=suggestion_filter,
        )
    else:
        visible_rows = build_catalogue_linking_rows(perfumes, min_score=min_score)
        visible_count = len(visible_rows)
        rows = filter_catalogue_linking_rows_by_confidence(
            visible_rows,
            confidence_filter,
        )
        rows = filter_catalogue_linking_rows_by_suggestion(
            rows,
            suggestion_filter,
        )
    selected_perfume_id = request.GET.get("perfume")
    selected_row = None
    for row in rows:
        if str(row["perfume"].id) == str(selected_perfume_id):
            selected_row = row
            break
    if selected_row is None and rows:
        selected_row = rows[0]
    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)
    return {
        "active_tab": "linking",
        "brands": brand_manager.annotate(perfume_count=Count("perfumes")).order_by(
            "name"
        ),
        "selected_brand": normalize_fragrantica_review_brand_id(
            request.GET.get("brand") or ""
        ),
        "search_query": request.GET.get("q", "").strip(),
        "status_filter": normalize_catalogue_linking_status(request.GET.get("status")),
        "suggestion_filter": suggestion_filter,
        "confidence_filter": confidence_filter,
        "min_score": min_score,
        "rows": rows,
        "suggestion_visible_count": visible_count,
        "suggestion_filtered_count": len(rows),
        "row_filter_active": row_filter_active,
        "selected_row": selected_row,
        "ready_bulk_count": sum(1 for row in rows if row["ready_for_bulk"]),
        "query_string": query_without_page.urlencode(),
    }


def build_catalogue_linking_candidate_payload(
    request,
    *,
    perfume_manager=None,
) -> tuple[dict, int]:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    perfume_id = request.GET.get("perfume")
    if not perfume_id:
        return {"error": "Choose an Our Products row first."}, 400
    perfume = first_from_queryset(
        perfume_manager.select_related("brand", "collection").filter(pk=perfume_id)
    )
    if not perfume:
        return {"error": "Our Products row was not found."}, 404
    min_score = normalize_catalogue_linking_min_score(request.GET.get("min_score"))
    linked_sources = build_linked_fragrantica_sources_by_perfume_ids(
        [perfume.id],
    ).get(perfume.id, [])
    if linked_sources:
        return (
            {
                "selected": {
                    "id": perfume.id,
                    "label": catalogue_linking_perfume_label(perfume),
                "brand": perfume.brand.name,
                "name": perfume.name,
                "collection": perfume.collection_name,
                "concentration": perfume.concentration,
                "audience": perfume.audience,
                "release_year": perfume.release_year,
            },
            "linked_sources": [
                serialize_catalogue_linking_source(source)
                for source in linked_sources
            ],
                "candidates": [],
            },
            200,
        )
    conflict_scope_perfumes = list(
        perfume_manager.select_related("brand", "collection").filter(
            brand=perfume.brand
        )
    )
    candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
        conflict_scope_perfumes,
        min_score=min_score,
        limit=12,
    )
    return (
        {
            "selected": {
                "id": perfume.id,
                "label": catalogue_linking_perfume_label(perfume),
                "brand": perfume.brand.name,
                "name": perfume.name,
                "collection": perfume.collection_name,
                "concentration": perfume.concentration,
                "audience": perfume.audience,
                "release_year": perfume.release_year,
            },
            "candidates": [
                serialize_catalogue_linking_candidate(candidate)
                for candidate in candidate_map.get(perfume.id, [])
            ],
            "linked_sources": [],
        },
        200,
    )


def run_catalogue_linking_bulk_action(
    post_data,
    *,
    host: str = "",
    source_manager=None,
    perfume_manager=None,
) -> CatalogueLinkingBulkResult:
    redirect_url = post_data.get(
        "next",
        reverse("prices:catalogue_linking_workbench"),
    )
    if not url_has_allowed_host_and_scheme(
        redirect_url,
        allowed_hosts={host} if host else None,
    ):
        redirect_url = reverse("prices:catalogue_linking_workbench")
    action = post_data.get("action")
    source_manager = source_manager or FragranticaProduct.objects
    perfume_manager = perfume_manager or CatalogPerfume.objects

    if action == "bulk_delete_perfumes":
        perfume_ids = (
            post_data.getlist("perfume_id")
            if hasattr(post_data, "getlist")
            else post_data.get("perfume_id", [])
        )
        if isinstance(perfume_ids, str):
            perfume_ids = [perfume_ids]
        clean_ids = [
            perfume_id for perfume_id in perfume_ids if str(perfume_id).isdigit()
        ]
        if not clean_ids:
            return CatalogueLinkingBulkResult(
                "error",
                "Select Our Products rows to delete.",
                redirect_url,
            )
        queryset = perfume_manager.filter(pk__in=clean_ids)
        deleted_count = queryset.count()
        if not deleted_count:
            return CatalogueLinkingBulkResult(
                "error",
                "Selected Our Products rows were not found.",
                redirect_url,
            )
        source_manager.filter(matched_perfume_id__in=clean_ids).update(
            matched_perfume=None,
            match_status=FragranticaProduct.STATUS_UNLINKED,
        )
        queryset.delete()
        return CatalogueLinkingBulkResult(
            "success",
            f"Deleted {deleted_count} Our Products perfume row(s).",
            redirect_url,
        )

    if action != "bulk_link":
        return CatalogueLinkingBulkResult(
            "error", "Unknown linking action.", redirect_url
        )

    selected_pairs = post_data.getlist("link_pair")
    if not selected_pairs:
        return CatalogueLinkingBulkResult(
            "error",
            "Select at least one suggested Fragrantica match.",
            redirect_url,
        )

    linked_count = 0
    skipped_count = 0
    for raw_pair in selected_pairs:
        parts = str(raw_pair).split(":")
        if len(parts) < 2:
            skipped_count += 1
            continue
        source_id, perfume_id = parts[0], parts[1]
        create_alias = len(parts) >= 3 and parts[2] == "1"
        source = first_from_queryset(
            source_manager.select_related("matched_perfume").filter(pk=source_id)
        )
        perfume = first_from_queryset(
            perfume_manager.select_related("brand").filter(pk=perfume_id)
        )
        if (
            not source
            or not perfume
            or not fragrantica_source_is_available_for_perfume(
                source,
                perfume,
            )
        ):
            skipped_count += 1
            continue
        result = run_fragrantica_catalogue_link_action(
            source.id,
            {
                "perfume_id": str(perfume.id),
                "next": redirect_url,
                "create_alias": "1" if create_alias else "0",
                "apply_identity_group": "1",
            },
            host=host,
        )
        if result.level == "success":
            linked_count += 1
        else:
            skipped_count += 1

    if linked_count:
        skipped_note = (
            f" Skipped {skipped_count} stale suggestion(s)." if skipped_count else ""
        )
        return CatalogueLinkingBulkResult(
            "success",
            f"Linked {linked_count} Fragrantica match(es).{skipped_note}",
            redirect_url,
        )
    return CatalogueLinkingBulkResult(
        "error",
        "No selected suggestions could be linked.",
        redirect_url,
    )


def build_fragrantica_candidate_map(
    fragrantica_rows,
    *,
    perfume_manager=None,
    brand_alias_manager=None,
    product_alias_manager=None,
) -> dict:
    fragrantica_rows = list(fragrantica_rows)
    candidate_choices = build_fragrantica_candidate_choices(
        fragrantica_rows,
        perfume_manager=perfume_manager,
        brand_alias_manager=brand_alias_manager,
        product_alias_manager=product_alias_manager,
        limit=1,
    )
    candidate_map: dict[object, CatalogPerfume] = {}
    for source in fragrantica_rows:
        choices = candidate_choices.get(_fragrantica_source_candidate_key(source), [])
        if not choices:
            continue
        perfume = choices[0].perfume
        candidate_map[_fragrantica_source_candidate_key(source)] = perfume
        candidate_map.setdefault(
            fragrantica_identity_key(
                source.brand_name,
                fragrantica_source_catalogue_name(source),
            ),
            perfume,
        )
    return candidate_map


def build_fragrantica_staging_context(
    request,
    *,
    fragrantica_manager=None,
    brand_manager=None,
    perfume_manager=None,
    row_limit: int = 25,
) -> dict:
    fragrantica_manager = fragrantica_manager or FragranticaProduct.objects
    brand_manager = brand_manager or CatalogBrand.objects
    queryset = fragrantica_manager.select_related(
        "matched_perfume",
        "matched_perfume__brand",
    ).order_by("match_status", "brand_name", "collection_name", "name")
    status_filter = normalize_fragrantica_review_status(
        request.GET.get("status", "all")
    )
    search_query = request.GET.get("q", "").strip()
    brand_id = normalize_fragrantica_review_brand_id(request.GET.get("brand") or "")
    if status_filter == "linked":
        queryset = queryset.filter(match_status=FragranticaProduct.STATUS_LINKED)
    elif status_filter in {"supplier_evidence", "collection_review", "catalog_only"}:
        queryset = queryset.filter(match_status=FragranticaProduct.STATUS_UNLINKED)
    if search_query:
        queryset = queryset.filter(fragrantica_product_search_filter(search_query))
    if brand_id:
        brand = first_from_queryset(brand_manager.filter(pk=brand_id))
        if brand:
            queryset = queryset.filter(
                normalized_brand_name=fragrantica_identity_key(brand.name, "")[0]
            )

    total_count = queryset.count()
    rows = list(queryset[:row_limit])
    candidate_map = build_fragrantica_candidate_map(
        rows,
        perfume_manager=perfume_manager,
    )
    staged_rows = []
    for row in rows:
        staged_rows.append(
            {
                "source": row,
                "display_name": fragrantica_source_catalogue_name(row),
                "candidate": row.matched_perfume
                or candidate_map.get(getattr(row, "id", None))
                or candidate_map.get(
                    fragrantica_identity_key(
                        row.brand_name,
                        fragrantica_source_catalogue_name(row),
                    )
                ),
            }
        )
    return {
        "fragrantica_staged_rows": staged_rows,
        "fragrantica_staged_count": total_count,
        "fragrantica_staged_limit": row_limit,
    }


def first_from_queryset(queryset):
    if hasattr(queryset, "first"):
        return queryset.first()
    return next(iter(queryset), None)


def _catalogue_collection_title_word(word: str, index: int) -> str:
    if not word:
        return word
    lower_word = word.lower()
    if index > 0 and lower_word in TITLECASE_LOWER_WORDS:
        return lower_word
    if word.upper() in COLLECTION_TITLECASE_ACRONYMS:
        return word.upper()
    if word.isupper() and ROMAN_NUMERAL_RE.match(word):
        return word
    return lower_word[:1].upper() + lower_word[1:]


def normalize_catalogue_collection_name(value: str) -> str:
    """Normalize reviewed external collection names before storing locally."""

    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return ""

    word_index = 0

    def replace_word(match: re.Match[str]) -> str:
        nonlocal word_index
        raw_word = match.group(0)
        apostrophe_parts = raw_word.split("'")
        normalized_parts = []
        for part_index, part in enumerate(apostrophe_parts):
            if part_index > 0 and part.lower() in TITLECASE_APOSTROPHE_SUFFIXES:
                normalized_parts.append(part.lower())
                continue
            normalized_parts.append(
                _catalogue_collection_title_word(
                    part,
                    word_index if part_index == 0 else 0,
                )
            )
        word_index += 1
        return "'".join(normalized_parts)

    return TITLE_WORD_RE.sub(replace_word, text)


def _catalogue_text_needs_title_case(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and all(char.isupper() for char in letters)


def normalize_catalogue_perfume_name(value: str) -> str:
    """Normalize reviewed external perfume names before storing/displaying locally."""

    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text or not _catalogue_text_needs_title_case(text):
        return text

    word_index = 0

    def replace_word(match: re.Match[str]) -> str:
        nonlocal word_index
        raw_word = match.group(0)
        normalized = _catalogue_collection_title_word(raw_word, word_index)
        word_index += 1
        return normalized

    return TITLE_WORD_RE.sub(replace_word, text)


def scent_name_audience_suffix_style(value: str) -> str:
    name_key = normalized_fragrance_key(value)
    if _contains_audience_term(name_key, "pour homme") or _contains_audience_term(
        name_key,
        "pour femme",
    ):
        return "pour"
    if audience_group_from_text(value):
        return "for"
    return ""


def display_name_without_terminal_audience(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return ""
    for term in sorted(AUDIENCE_NAME_TERMS, key=len, reverse=True):
        term_pattern = re.escape(term).replace(r"\ ", r"\s+")
        cleaned = re.sub(
            rf"(?i)(?:\s+|[\s\-/\(]+){term_pattern}\)?\s*$",
            "",
            text,
        )
        cleaned = re.sub(
            rf"(?i)^\(?{term_pattern}\)?(?:\s+|[\s\-/]+)",
            "",
            cleaned,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -/()")
        if cleaned != text:
            return cleaned or text
    return text


def catalogue_audience_base_key(value: str) -> str:
    return fragrance_name_without_audience_or_concentration(value)


def fragrantica_source_same_base_audience_context(
    source,
    perfume,
    *,
    perfume_manager=None,
    fragrantica_manager=None,
) -> tuple[set[str], set[str]]:
    source_name = fragrantica_source_catalogue_name(source)
    source_base = catalogue_audience_base_key(source_name)
    if not source_base:
        return set(), set()
    perfume_manager = perfume_manager or CatalogPerfume.objects
    fragrantica_manager = fragrantica_manager or FragranticaProduct.objects
    groups = {
        audience_group_from_text(source.audience, source_name),
        audience_group_from_text(perfume.audience, perfume.name),
    }
    styles = {scent_name_audience_suffix_style(source_name)}
    for candidate in perfume_manager.filter(brand=perfume.brand).only(
        "name",
        "audience",
    ):
        if catalogue_audience_base_key(candidate.name) != source_base:
            continue
        groups.add(audience_group_from_text(candidate.audience, candidate.name))
        styles.add(scent_name_audience_suffix_style(candidate.name))

    brand_key = normalized_fragrance_key(source.brand_name or perfume.brand.name)
    source_queryset = fragrantica_manager.filter(normalized_brand_name=brand_key)
    if not source_queryset.exists():
        source_queryset = fragrantica_manager.filter(brand_name__iexact=source.brand_name)
    for candidate_source in source_queryset.only(
        "brand_name",
        "name",
        "normalized_name",
        "collection_name",
        "audience",
    ):
        for candidate_name in _fragrantica_source_match_names(candidate_source):
            if catalogue_audience_base_key(candidate_name) != source_base:
                continue
            groups.add(
                audience_group_from_text(candidate_source.audience, candidate_name)
            )
            styles.add(scent_name_audience_suffix_style(candidate_name))
            break
    groups.discard("")
    styles.discard("")
    return groups, styles


def reviewed_fragrantica_perfume_name(source, perfume) -> str:
    source_name = fragrantica_source_catalogue_name(source)
    source_group = audience_group_from_text(source.audience, source_name)
    if not source_name or not source_group:
        return source_name
    groups, styles = fragrantica_source_same_base_audience_context(source, perfume)
    if not {"men", "women"}.issubset(groups):
        return source_name
    suffix_style = "pour" if "pour" in styles else "for"
    suffix = AUDIENCE_DISPLAY_SUFFIXES[source_group][suffix_style]
    if _contains_audience_term(normalized_fragrance_key(source_name), suffix.lower()):
        return source_name
    base_name = display_name_without_terminal_audience(source_name)
    if not base_name or normalized_fragrance_key(base_name) == normalized_fragrance_key(
        suffix
    ):
        return source_name
    return f"{base_name} {suffix}"


def apply_fragrantica_identity_to_perfume(source, perfume) -> list[str]:
    changed_fields = []
    reviewed_name = reviewed_fragrantica_perfume_name(source, perfume)
    source_name_without_concentration = display_name_without_concentration(
        reviewed_name
    )
    source_name = normalize_catalogue_perfume_name(
        source_name_without_concentration or reviewed_name
    )
    if source_name and perfume.name != source_name:
        perfume.name = source_name
        changed_fields.append("name")
    source_collection_name = normalize_catalogue_collection_name(source.collection_name)
    if source_collection_name and perfume.collection_name != source_collection_name:
        perfume.collection_name = source_collection_name
        changed_fields.append("collection_name")
    source_collection = None
    if source_collection_name:
        source_collection = get_or_create_collection(
            perfume.brand, source_collection_name
        )
    if source_collection and perfume.collection_id != source_collection.id:
        perfume.collection = source_collection
        changed_fields.append("collection")
    if source.audience and perfume.audience != source.audience:
        perfume.audience = source.audience
        changed_fields.append("audience")
    if source.release_year and perfume.release_year != source.release_year:
        perfume.release_year = source.release_year
        changed_fields.append("release_year")
    if changed_fields:
        changed_fields.append("updated_at")
        perfume.save(update_fields=changed_fields)
    return changed_fields


def build_fragrantica_identity_perfume_group(
    perfume,
    *,
    source=None,
    perfume_manager=None,
):
    perfume_manager = perfume_manager or CatalogPerfume.objects
    name_key = normalized_fragrance_key(perfume.name)
    source_group = (
        audience_group_from_text(source.audience, fragrantica_source_catalogue_name(source))
        if source
        else ""
    )
    group = []
    for candidate in perfume_manager.filter(brand=perfume.brand).select_related(
        "brand"
    ):
        if normalized_fragrance_key(candidate.name) != name_key:
            continue
        if source_group:
            candidate_group = audience_group_from_text(
                candidate.audience,
                candidate.name,
            )
            if candidate_group and candidate_group != source_group:
                continue
        group.append(candidate)
    return group or [perfume]


def upsert_fragrantica_catalog_source(source, perfume) -> bool:
    source_url = source.source_href
    if not source_url:
        return False
    source_name = fragrantica_source_catalogue_name(source)
    catalog_source, created = CatalogSource.objects.get_or_create(
        perfume=perfume,
        url=source_url,
        defaults={
            "title": f"Fragrantica: {source.brand_name} / {source_name}",
            "source_type": CatalogSource.SOURCE_COMMUNITY,
            "source_domain": source.source_domain or "fragrantica.com",
            "priority_rank": 30,
            "reliability": CatalogSource.RELIABILITY_MEDIUM,
        },
    )
    update_fields = []
    if not created:
        desired_values = {
            "title": f"Fragrantica: {source.brand_name} / {source_name}",
            "source_type": CatalogSource.SOURCE_COMMUNITY,
            "source_domain": source.source_domain or "fragrantica.com",
            "priority_rank": 30,
            "reliability": CatalogSource.RELIABILITY_MEDIUM,
        }
        for field, value in desired_values.items():
            if getattr(catalog_source, field) != value:
                setattr(catalog_source, field, value)
                update_fields.append(field)
        if update_fields:
            catalog_source.save(update_fields=[*update_fields, "last_checked_at"])
    return True


def create_fragrantica_product_alias_if_needed(source, perfume, old_name: str) -> bool:
    alias_text = (old_name or "").strip()
    canonical_text = fragrantica_source_catalogue_name(source).strip()
    if not alias_text or not canonical_text:
        return False
    if normalized_fragrance_key(alias_text) == normalized_fragrance_key(canonical_text):
        return False
    existing = ProductAlias.objects.filter(
        brand=perfume.brand,
        alias_text__iexact=alias_text,
        canonical_text__iexact=canonical_text,
        active=True,
    ).first()
    if existing:
        if not existing.perfume_id:
            existing.perfume = perfume
            existing.save(update_fields=["perfume", "updated_at"])
        return False
    ProductAlias.objects.create(
        perfume=perfume,
        brand=perfume.brand,
        alias_text=alias_text,
        canonical_text=canonical_text,
        collection_name=normalize_catalogue_collection_name(source.collection_name),
        audience=source.audience,
        priority=50,
    )
    return True


def run_fragrantica_catalogue_link_action(
    source_id,
    post_data,
    *,
    host: str = "",
    source_getter=None,
    perfume_getter=None,
) -> FragranticaCatalogueLinkResult:
    if source_getter is None:

        def source_getter(pk):
            return get_object_or_404(FragranticaProduct, pk=pk)

    if perfume_getter is None:

        def perfume_getter(pk):
            return get_object_or_404(
                CatalogPerfume.objects.select_related("brand"),
                pk=pk,
            )

    redirect_url = post_data.get("next", reverse("prices:fragrantica_product_review"))
    if not url_has_allowed_host_and_scheme(
        redirect_url,
        allowed_hosts={host} if host else None,
    ):
        redirect_url = reverse("prices:fragrantica_product_review")

    source = source_getter(source_id)
    perfume_id = post_data.get("perfume_id")
    if not perfume_id:
        return FragranticaCatalogueLinkResult(
            "error",
            "Choose an Our Products catalogue match before linking.",
            redirect_url,
        )
    perfume = perfume_getter(perfume_id)
    if source.matched_perfume_id and source.matched_perfume_id != perfume.id:
        linked_name = (
            catalogue_linking_perfume_label(source.matched_perfume)
            if getattr(source, "matched_perfume", None)
            else "another Our Products row"
        )
        return FragranticaCatalogueLinkResult(
            "error",
            f"This Fragrantica row is already linked to {linked_name}. Review manually before changing it.",
            redirect_url,
        )
    old_name = perfume.name
    create_alias = post_data.get("create_alias") == "1"
    apply_identity_group = post_data.get("apply_identity_group") == "1"
    perfume_group = (
        build_fragrantica_identity_perfume_group(perfume, source=source)
        if apply_identity_group
        else [perfume]
    )
    source.matched_perfume = perfume
    source.match_status = FragranticaProduct.STATUS_LINKED
    source.save(update_fields=["matched_perfume", "match_status", "updated_at"])
    changed_field_names = set()
    source_count = 0
    for group_perfume in perfume_group:
        changed_fields = apply_fragrantica_identity_to_perfume(source, group_perfume)
        changed_field_names.update(
            field for field in changed_fields if field != "updated_at"
        )
        if upsert_fragrantica_catalog_source(source, group_perfume):
            source_count += 1
    alias_created = (
        create_fragrantica_product_alias_if_needed(source, perfume, old_name)
        if create_alias
        else False
    )
    preserved_note = " Concentration and variants were preserved."
    changed_note = (
        f" Updated catalogue fields: {', '.join(sorted(changed_field_names))}."
        if changed_field_names
        else " Catalogue identity already matched."
    )
    group_note = (
        f" Applied to {len(perfume_group)} concentration rows."
        if len(perfume_group) > 1
        else ""
    )
    source_note = (
        f" Added Fragrantica source link to {source_count} row(s)."
        if source_count
        else ""
    )
    alias_note = " Saved old local name as a product alias." if alias_created else ""
    return FragranticaCatalogueLinkResult(
        "success",
        f"Linked Fragrantica row to {perfume.brand.name} / {perfume.name}."
        f"{changed_note}{group_note}{source_note}{alias_note}{preserved_note}",
        redirect_url,
    )


def build_catalog_tab_action_result(
    post_data,
    *,
    brand_manager=None,
    perfume_manager=None,
    variant_manager=None,
    collection_getter=get_or_create_collection,
) -> CatalogTabActionResult:
    brand_manager = brand_manager or CatalogBrand.objects
    perfume_manager = perfume_manager or CatalogPerfume.objects
    variant_manager = variant_manager or CatalogPerfumeVariant.objects
    action = post_data.get("action", "").strip()
    tab = post_data.get("tab", "products").strip() or "products"

    if action == "bulk_delete_variants":
        variant_ids = (
            post_data.getlist("variant_id")
            if hasattr(post_data, "getlist")
            else post_data.get("variant_id", [])
        )
        if isinstance(variant_ids, str):
            variant_ids = [variant_ids]
        clean_ids = [
            variant_id for variant_id in variant_ids if str(variant_id).isdigit()
        ]
        if not clean_ids:
            return CatalogTabActionResult(
                "error", "Select product rows to delete.", tab
            )
        queryset = variant_manager.filter(pk__in=clean_ids)
        deleted_count = queryset.count()
        if not deleted_count:
            return CatalogTabActionResult(
                "error", "Selected product rows were not found.", tab
            )
        queryset.delete()
        return CatalogTabActionResult(
            "success", f"Deleted {deleted_count} catalogue variant row(s).", tab
        )

    if action == "add_brand":
        name = post_data.get("name", "").strip()
        if not name:
            return CatalogTabActionResult("error", "Brand name is required.", tab)
        brand, created = brand_manager.get_or_create(name=name)
        return CatalogTabActionResult(
            "success",
            f"Brand {'created' if created else 'already exists'}: {brand.name}.",
            tab,
        )

    if action == "rename_brand":
        brand = brand_manager.filter(pk=post_data.get("brand_id")).first()
        new_value = post_data.get("new_value", "").strip()
        if not brand:
            raise Http404("Brand not found.")
        if not new_value:
            return CatalogTabActionResult("error", "New brand name is required.", tab)
        old_name = brand.name
        if old_name == new_value:
            return CatalogTabActionResult(
                "success", f"Brand unchanged: {old_name}.", tab
            )
        existing_brand = (
            brand_manager.filter(name__iexact=new_value).exclude(pk=brand.pk).first()
        )
        if existing_brand:
            return CatalogTabActionResult(
                "error",
                f"Brand already exists: {new_value}.",
                tab,
            )
        brand.name = new_value
        try:
            brand.save(update_fields=["name", "updated_at"])
        except IntegrityError:
            brand.name = old_name
            return CatalogTabActionResult(
                "error",
                f"Brand already exists: {new_value}.",
                tab,
            )
        return CatalogTabActionResult(
            "success",
            f"Brand renamed: {old_name} -> {brand.name}. Products using this brand now show the new name.",
            tab,
        )

    if action == "delete_brand":
        brand = brand_manager.filter(pk=post_data.get("brand_id")).first()
        if not brand:
            raise Http404("Brand not found.")
        if perfume_manager.filter(brand=brand).exists():
            return CatalogTabActionResult(
                "error",
                f"{brand.name} has products. Move or delete those products first.",
                tab,
            )
        brand_name = brand.name
        brand.delete()
        return CatalogTabActionResult("success", f"Brand deleted: {brand_name}.", tab)

    if action in {"rename_collection", "clear_collection"}:
        brand = brand_manager.filter(pk=post_data.get("brand_id")).first()
        old_value = post_data.get("old_value", "").strip()
        new_value = post_data.get("new_value", "").strip()
        if not brand:
            return CatalogTabActionResult("error", "Select a collection brand.", tab)
        if not old_value:
            return CatalogTabActionResult("error", "Select a collection.", tab)
        queryset = perfume_manager.filter(brand=brand, collection_name=old_value)
        if action == "rename_collection":
            if not new_value:
                return CatalogTabActionResult(
                    "error", "New collection name is required.", tab
                )
            collection = collection_getter(brand, new_value)
            update_data = {"collection_name": new_value}
            if collection:
                update_data["collection"] = collection
            updated = queryset.update(**update_data)
            return CatalogTabActionResult(
                "success",
                f"Collection renamed on {updated} {brand.name} products.",
                tab,
            )
        updated = queryset.update(collection_name="", collection=None)
        return CatalogTabActionResult(
            "success", f"Collection cleared on {updated} {brand.name} products.", tab
        )

    if action in {"rename_concentration", "clear_concentration"}:
        old_value = post_data.get("old_value", "").strip()
        new_value = post_data.get("new_value", "").strip()
        if not old_value:
            return CatalogTabActionResult("error", "Select a concentration.", tab)
        if action == "rename_concentration":
            if not new_value:
                return CatalogTabActionResult(
                    "error", "New concentration name is required.", tab
                )
            updated = perfume_manager.filter(concentration=old_value).update(
                concentration=new_value
            )
            return CatalogTabActionResult(
                "success", f"Concentration renamed on {updated} products.", tab
            )
        updated = perfume_manager.filter(concentration=old_value).update(
            concentration=""
        )
        return CatalogTabActionResult(
            "success", f"Concentration cleared on {updated} products.", tab
        )

    return CatalogTabActionResult("error", "Unknown catalogue action.", tab)


def run_catalog_tab_post_action(
    post_data,
    *,
    host: str = "",
    action_builder=build_catalog_tab_action_result,
) -> CatalogTabPostActionResult:
    result = action_builder(post_data)
    redirect_url = post_data.get("next", "").strip()
    if not redirect_url or not url_has_allowed_host_and_scheme(
        redirect_url,
        allowed_hosts={host} if host else None,
    ):
        redirect_url = (
            f"{reverse('prices:our_product_list')}?{urlencode({'tab': result.tab})}"
        )
    return CatalogTabPostActionResult(
        level=result.level,
        message=result.message,
        redirect_url=redirect_url,
    )


def build_our_product_detail_offers_queryset(
    request,
    our_product,
    *,
    supplier_product_manager=None,
):
    supplier_product_manager = (
        supplier_product_manager or models.SupplierProduct.objects
    )
    offers = supplier_product_manager.select_related("supplier").filter(
        our_product=our_product
    )
    return apply_hidden_product_keywords(
        offers,
        parse_exclude_terms(resolve_supplier_exclude_terms(request)),
    )


def build_our_product_detail_context(
    request,
    our_product,
    *,
    offers_builder=build_our_product_detail_offers_queryset,
) -> dict:
    return {
        "offers": offers_builder(request, our_product),
    }


def catalogue_review_name_key(value: str) -> str:
    return normalize_alias_value(value or "")


def normalize_fragrantica_review_status(value: str | None) -> str:
    status = (value or "all").strip() or "all"
    if status not in FRAGRANTICA_REVIEW_STATUSES:
        return "all"
    return status


def fragrantica_review_row_status(perfume, evidence: Iterable) -> tuple[str, str]:
    evidence_list = list(evidence)
    linked_count = getattr(perfume, "linked_supplier_count", 0)
    perfume_collection = catalogue_review_name_key(
        getattr(perfume, "collection_name", "")
    )
    evidence_collections = {
        catalogue_review_name_key(getattr(item, "collection_name", ""))
        for item in evidence_list
        if getattr(item, "collection_name", "")
    }
    has_collection_conflict = bool(
        perfume_collection
        and evidence_collections
        and any(collection != perfume_collection for collection in evidence_collections)
    )
    if has_collection_conflict:
        return "collection_review", "Collection review"
    if linked_count:
        return "linked", "Linked"
    if evidence_list:
        return "supplier_evidence", "Supplier evidence"
    return "catalog_only", "Catalogue only"


def build_missing_supplier_rows(
    evidence_by_name: dict, catalogue_keys: set[tuple[int, str]]
) -> list[dict]:
    missing_supplier_rows = []
    for (parsed_brand_id, name_key), evidence in evidence_by_name.items():
        if (parsed_brand_id, name_key) in catalogue_keys:
            continue
        first = evidence[0]
        missing_supplier_rows.append(
            {
                "brand": first.normalized_brand,
                "name": first.display_product_name or first.product_name_text,
                "collections": sorted(
                    {item.collection_name for item in evidence if item.collection_name}
                ),
                "count": len(evidence),
                "sample": evidence[:3],
            }
        )
    return sorted(
        missing_supplier_rows, key=lambda row: (str(row["brand"]), row["name"])
    )


def parse_catalog_variant_size(raw_value: str) -> tuple[Decimal | None, str]:
    raw_size = (raw_value or "").strip()
    normalized = raw_size.lower().replace("ml", "").replace(",", ".").strip()
    if not normalized:
        return None, ""
    try:
        return Decimal(normalized), ""
    except (InvalidOperation, ValueError):
        return None, raw_size


def build_catalog_variant_inline_update(post_data) -> CatalogVariantInlineUpdate:
    size_ml, size_label = parse_catalog_variant_size(post_data.get("size_ml", ""))
    return CatalogVariantInlineUpdate(
        brand_name=post_data.get("brand_name", "").strip(),
        perfume_name=post_data.get("perfume_name", "").strip(),
        collection_name=post_data.get("collection_name", "").strip(),
        concentration=post_data.get("concentration", "").strip(),
        size_ml=size_ml,
        size_label=size_label,
        is_tester=post_data.get("is_tester") == "1",
        packaging=post_data.get("packaging", "").strip(),
        variant_type=post_data.get("variant_type", "").strip() or "standard",
    )


def apply_catalog_variant_inline_update(
    variant,
    post_data,
    *,
    brand_manager=None,
) -> CatalogVariantInlineUpdateResult:
    update_data = build_catalog_variant_inline_update(post_data)

    if not update_data.brand_name or not update_data.perfume_name:
        return CatalogVariantInlineUpdateResult(
            "error",
            "Brand and scent are required.",
        )

    brand_manager = brand_manager or CatalogBrand.objects
    brand = brand_manager.filter(name__iexact=update_data.brand_name).first()
    if not brand:
        return CatalogVariantInlineUpdateResult(
            "error",
            "Choose an existing brand from the catalogue.",
        )

    perfume = variant.perfume
    perfume.brand = brand
    perfume.name = update_data.perfume_name
    perfume.collection_name = update_data.collection_name
    perfume.concentration = update_data.concentration
    perfume.save(
        update_fields=[
            "brand",
            "name",
            "collection",
            "collection_name",
            "concentration",
            "updated_at",
        ]
    )

    variant.size_ml = update_data.size_ml
    variant.size_label = update_data.size_label
    variant.is_tester = update_data.is_tester
    variant.packaging = update_data.packaging
    variant.variant_type = update_data.variant_type
    variant.save(
        update_fields=[
            "size_ml",
            "size_label",
            "is_tester",
            "packaging",
            "variant_type",
            "updated_at",
        ]
    )
    return CatalogVariantInlineUpdateResult("success", "Product row updated.")


def catalog_variant_inline_update_redirect_url(
    next_url_raw: str | None,
    *,
    host: str,
    fallback_url=None,
) -> str:
    next_url = next_url_raw or ""
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={host} if host else set(),
    ):
        return next_url
    return str(fallback_url or reverse_lazy("prices:our_product_list"))


def run_catalog_variant_inline_update_action(
    variant_id,
    post_data,
    *,
    host: str,
    variant_getter=None,
    update_func=apply_catalog_variant_inline_update,
) -> CatalogVariantInlineUpdateActionResult:
    redirect_url = catalog_variant_inline_update_redirect_url(
        post_data.get("next") or "",
        host=host,
    )
    if variant_getter is None:

        def variant_getter(pk):
            return get_object_or_404(
                CatalogPerfumeVariant.objects.select_related(
                    "perfume",
                    "perfume__brand",
                ),
                pk=pk,
            )

    result = update_func(variant_getter(variant_id), post_data)
    return CatalogVariantInlineUpdateActionResult(
        level=result.level,
        message=result.message,
        redirect_url=redirect_url,
    )
