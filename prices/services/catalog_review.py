from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from functools import lru_cache
from urllib.parse import urlencode

import regex
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from assistant_linking.models import BrandAlias
from assistant_linking.models import FragranticaProduct
from assistant_linking.models import ParsedSupplierProduct
from assistant_linking.models import ProductAlias
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
CATALOGUE_LINKING_STATUSES = {"all", "unlinked", "linked"}
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
FRAGRANCE_CONCENTRATION_NAME_TERMS = {
    "eau de parfum",
    "eau de toilette",
    "eau de cologne",
    "extrait de parfum",
    "edp",
    "edt",
    "edc",
}
CATALOGUE_LINKING_DEFAULT_MIN_SCORE = 90


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
    token: str,
    *,
    search_fields: tuple[str, ...] = CATALOG_VARIANT_SEARCH_FIELDS,
) -> Q:
    token_filter = Q()
    for field in search_fields:
        token_filter |= Q(**{f"{field}__icontains": token})
    return token_filter


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
        .values("collection_name")
        .annotate(perfume_count=Count("id"))
        .order_by("collection_name")
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
    return normalize_alias_value(value or "").replace("&", "and")


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
        candidate.source.name,
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
        source_name_key = source.normalized_name or normalized_fragrance_key(
            source.name
        )
        creates_alias = source_name_key != name_key
        if source_name_key == name_key:
            add_fragrantica_candidate(
                candidates,
                source,
                match_type="exact",
                score=100,
                reason="Exact brand and scent match",
                creates_alias=False,
            )
            continue

        source_base = fragrance_name_without_audience(source.name)
        source_audience_group = audience_group_from_text(source.audience, source.name)
        audience_compatible = (
            not audience_group
            or not source_audience_group
            or audience_group == source_audience_group
        )
        if (
            name_base
            and source_base
            and name_base == source_base
            and audience_compatible
        ):
            add_fragrantica_candidate(
                candidates,
                source,
                match_type="name hint",
                score=92,
                reason="Same brand and scent after audience words",
                creates_alias=creates_alias,
            )
            continue

        ratio = SequenceMatcher(None, name_key, source_name_key).ratio()
        base_ratio = (
            SequenceMatcher(None, name_base, source_base).ratio()
            if name_base and source_base
            else 0
        )
        best_ratio = max(ratio, base_ratio)
        if audience_compatible and best_ratio >= 0.82:
            add_fragrantica_candidate(
                candidates,
                source,
                match_type="fuzzy",
                score=int(best_ratio * 100),
                reason="Similar same-brand scent name",
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
            source_name_key = source.normalized_name or normalized_fragrance_key(
                source.name
            )
            if source_name_key in expected_source_keys:
                add_fragrantica_candidate(
                    candidates,
                    source,
                    match_type="alias",
                    score=96,
                    reason="Matched by product alias knowledge",
                    creates_alias=source_name_key != name_key,
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
    candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
        perfumes.values(),
        fragrantica_manager=fragrantica_manager,
        product_alias_manager=product_alias_manager,
        min_score=0,
        limit=3,
    )
    for variant in variants:
        variant.fragrantica_candidates = candidate_map.get(variant.perfume_id, [])
    return variants


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
        search_filter = (
            Q(brand_name__icontains=search_query)
            | Q(name__icontains=search_query)
            | Q(collection_name__icontains=search_query)
            | Q(audience__icontains=search_query)
            | Q(source_path__icontains=search_query)
            | Q(source_url__icontains=search_query)
        )
        if search_query.isdigit():
            search_filter |= Q(release_year=int(search_query))
        filtered_queryset = filtered_queryset.filter(search_filter)

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
        source.name,
        getattr(source, "normalized_name", ""),
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
    source_precise_name_keys = fragrance_precise_identity_match_keys(
        source.name,
        getattr(source, "normalized_name", ""),
        regex_preprocess_rules=regex_preprocess_rules,
    )
    perfume_precise_name_keys = fragrance_precise_identity_match_keys(
        perfume.name,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    if source_precise_name_keys & perfume_precise_name_keys:
        return 100, "Exact brand and scent identity match"

    source_name_keys = fragrance_loose_identity_match_keys(
        source.name,
        getattr(source, "normalized_name", ""),
        regex_preprocess_rules=regex_preprocess_rules,
    )
    perfume_name_keys = fragrance_loose_identity_match_keys(
        perfume.name,
        regex_preprocess_rules=regex_preprocess_rules,
    )
    if source_name_keys & perfume_name_keys:
        return 98, "Exact brand and scent identity match"

    for alias in aliases:
        if _product_alias_supports_fragrantica_source(
            source,
            perfume,
            alias,
            regex_preprocess_rules=regex_preprocess_rules,
        ):
            return 96, "Matched by product alias knowledge"

    source_base = fragrance_name_without_audience(source.name)
    perfume_base = fragrance_name_without_audience(perfume.name)
    source_loose_base = loose_fragrance_name_without_audience(source.name)
    perfume_loose_base = loose_fragrance_name_without_audience(perfume.name)
    source_identity_base = fragrance_name_without_audience_or_concentration(source.name)
    perfume_identity_base = fragrance_name_without_audience_or_concentration(
        perfume.name
    )
    source_loose_identity_base = loose_fragrance_name_without_audience_or_concentration(
        source.name
    )
    perfume_loose_identity_base = (
        loose_fragrance_name_without_audience_or_concentration(perfume.name)
    )
    source_audience_group = audience_group_from_text(source.audience, source.name)
    perfume_audience_group = audience_group_from_text(perfume.audience, perfume.name)
    audience_compatible = (
        not source_audience_group
        or not perfume_audience_group
        or source_audience_group == perfume_audience_group
    )

    if audience_compatible and source_base and perfume_base:
        if source_base == perfume_base:
            return 94, "Same brand and scent after audience words"
        if (
            source_loose_base
            and perfume_loose_base
            and source_loose_base == perfume_loose_base
        ):
            return 93, "Same brand and scent after punctuation and audience words"
        if source_identity_base and perfume_identity_base:
            if source_identity_base == perfume_identity_base:
                return 95, "Same brand and scent after concentration words"
            if (
                source_loose_identity_base
                and perfume_loose_identity_base
                and source_loose_identity_base == perfume_loose_identity_base
            ):
                return (
                    94,
                    "Same brand and scent after punctuation and concentration words",
                )

    ratios = [
        SequenceMatcher(
            None,
            normalized_fragrance_key(source.name),
            normalized_fragrance_key(perfume.name),
        ).ratio(),
        SequenceMatcher(
            None, loose_fragrance_key(source.name), loose_fragrance_key(perfume.name)
        ).ratio(),
    ]
    if source_base and perfume_base:
        ratios.append(SequenceMatcher(None, source_base, perfume_base).ratio())
    if source_loose_base and perfume_loose_base:
        ratios.append(
            SequenceMatcher(None, source_loose_base, perfume_loose_base).ratio()
        )
    if source_identity_base and perfume_identity_base:
        ratios.append(
            SequenceMatcher(None, source_identity_base, perfume_identity_base).ratio()
        )
    if source_loose_identity_base and perfume_loose_identity_base:
        ratios.append(
            SequenceMatcher(
                None,
                source_loose_identity_base,
                perfume_loose_identity_base,
            ).ratio()
        )
    best_ratio = max(ratios)
    if audience_compatible and best_ratio >= 0.82:
        return int(best_ratio * 100), "Similar same-brand scent name"
    return 0, ""


def _fragrantica_candidate_sort_key(item):
    source, perfume, score, _reason = item
    audience_group = audience_group_from_text(source.audience, source.name)
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
    return fragrantica_identity_key(source.brand_name, source.name)


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
                    normalized_fragrance_key(source.name)
                    != normalized_fragrance_key(perfume.name)
                ),
            )

    return {
        perfume_id: sorted(candidates.values(), key=_candidate_sort_key)[:limit]
        for perfume_id, candidates in candidates_by_perfume.items()
    }


def catalogue_linking_perfume_label(perfume) -> str:
    parts = [
        perfume.brand.name,
        perfume.collection_name,
        perfume.name,
        perfume.concentration,
    ]
    return " / ".join(part for part in parts if part)


def catalogue_linking_source_label(source) -> str:
    parts = [
        source.brand_name,
        source.collection_name,
        source.name,
        source.audience,
        str(source.release_year) if source.release_year else "",
    ]
    return " / ".join(part for part in parts if part)


def serialize_catalogue_linking_candidate(candidate: FragranticaMatchCandidate) -> dict:
    source = candidate.source
    return {
        "source_id": source.id,
        "label": catalogue_linking_source_label(source),
        "brand": source.brand_name,
        "name": source.name,
        "collection": source.collection_name,
        "audience": source.audience,
        "release_year": source.release_year,
        "score": candidate.score,
        "reason": candidate.reason,
        "match_type": candidate.match_type,
        "creates_alias": candidate.creates_alias,
        "match_status": source.match_status,
        "source_href": source.source_href,
        "link_url": reverse("prices:fragrantica_product_link", args=[source.pk]),
        "review_url": f"{reverse('prices:fragrantica_product_review')}?{urlencode({'q': source.name})}",
    }


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
    candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
        perfumes,
        min_score=min_score,
        limit=5,
    )
    rows = []
    for perfume in perfumes:
        candidates = candidate_map.get(perfume.id, [])
        top_candidate = candidates[0] if candidates else None
        rows.append(
            {
                "perfume": perfume,
                "label": catalogue_linking_perfume_label(perfume),
                "candidates": candidates,
                "top_candidate": top_candidate,
                "ready_for_bulk": bool(
                    top_candidate
                    and top_candidate.score >= min_score
                    and top_candidate.source.match_status
                    != FragranticaProduct.STATUS_LINKED
                ),
            }
        )
    return rows


def build_catalogue_linking_context(
    request,
    list_context: dict,
    *,
    brand_manager=None,
) -> dict:
    brand_manager = brand_manager or CatalogBrand.objects
    min_score = normalize_catalogue_linking_min_score(request.GET.get("min_score"))
    perfumes = list(list_context.get("perfumes", []))
    rows = build_catalogue_linking_rows(perfumes, min_score=min_score)
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
        "min_score": min_score,
        "rows": rows,
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
    candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
        [perfume],
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
            },
            "candidates": [
                serialize_catalogue_linking_candidate(candidate)
                for candidate in candidate_map.get(perfume.id, [])
            ],
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
    if post_data.get("action") != "bulk_link":
        return CatalogueLinkingBulkResult(
            "error", "Unknown linking action.", redirect_url
        )

    source_manager = source_manager or FragranticaProduct.objects
    perfume_manager = perfume_manager or CatalogPerfume.objects
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
            fragrantica_identity_key(source.brand_name, source.name),
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
        queryset = queryset.filter(
            Q(brand_name__icontains=search_query)
            | Q(name__icontains=search_query)
            | Q(collection_name__icontains=search_query)
            | Q(audience__icontains=search_query)
        )
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
                "candidate": row.matched_perfume
                or candidate_map.get(getattr(row, "id", None))
                or candidate_map.get(
                    fragrantica_identity_key(row.brand_name, row.name)
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


def apply_fragrantica_identity_to_perfume(source, perfume) -> list[str]:
    changed_fields = []
    if source.name and perfume.name != source.name:
        perfume.name = source.name
        changed_fields.append("name")
    if source.collection_name and perfume.collection_name != source.collection_name:
        perfume.collection_name = source.collection_name
        changed_fields.append("collection_name")
    source_collection = getattr(source, "collection", None)
    if not source_collection and source.collection_name:
        source_collection = get_or_create_collection(
            perfume.brand, source.collection_name
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


def build_fragrantica_identity_perfume_group(perfume, *, perfume_manager=None):
    perfume_manager = perfume_manager or CatalogPerfume.objects
    name_key = normalized_fragrance_key(perfume.name)
    group = []
    for candidate in perfume_manager.filter(brand=perfume.brand).select_related(
        "brand"
    ):
        if normalized_fragrance_key(candidate.name) == name_key:
            group.append(candidate)
    return group or [perfume]


def upsert_fragrantica_catalog_source(source, perfume) -> bool:
    source_url = source.source_href
    if not source_url:
        return False
    catalog_source, created = CatalogSource.objects.get_or_create(
        perfume=perfume,
        url=source_url,
        defaults={
            "title": f"Fragrantica: {source.brand_name} / {source.name}",
            "source_type": CatalogSource.SOURCE_COMMUNITY,
            "source_domain": source.source_domain or "fragrantica.com",
            "priority_rank": 30,
            "reliability": CatalogSource.RELIABILITY_MEDIUM,
        },
    )
    update_fields = []
    if not created:
        desired_values = {
            "title": f"Fragrantica: {source.brand_name} / {source.name}",
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
    canonical_text = (source.name or "").strip()
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
        collection_name=source.collection_name,
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
    old_name = perfume.name
    create_alias = post_data.get("create_alias") == "1"
    apply_identity_group = post_data.get("apply_identity_group") == "1"
    perfume_group = (
        build_fragrantica_identity_perfume_group(perfume)
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
) -> CatalogTabActionResult:
    brand_manager = brand_manager or CatalogBrand.objects
    perfume_manager = perfume_manager or CatalogPerfume.objects
    action = post_data.get("action", "").strip()
    tab = post_data.get("tab", "products").strip() or "products"

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
        old_value = post_data.get("old_value", "").strip()
        new_value = post_data.get("new_value", "").strip()
        if not old_value:
            return CatalogTabActionResult("error", "Select a collection.", tab)
        if action == "rename_collection":
            if not new_value:
                return CatalogTabActionResult(
                    "error", "New collection name is required.", tab
                )
            updated = perfume_manager.filter(collection_name=old_value).update(
                collection_name=new_value
            )
            return CatalogTabActionResult(
                "success", f"Collection renamed on {updated} products.", tab
            )
        updated = perfume_manager.filter(collection_name=old_value).update(
            collection_name=""
        )
        return CatalogTabActionResult(
            "success", f"Collection cleared on {updated} products.", tab
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
    action_builder=build_catalog_tab_action_result,
) -> CatalogTabPostActionResult:
    result = action_builder(post_data)
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
