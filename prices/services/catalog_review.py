from __future__ import annotations

import json
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
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import urlencode

import regex
from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from assistant_linking.models import AIRecommendation
from assistant_linking.models import BrandAlias
from assistant_linking.models import FragranticaProduct
from assistant_linking.models import FragranticaProductLink
from assistant_linking.models import normalized_fragrantica_product_name
from assistant_linking.models import ParsedSupplierProduct
from assistant_linking.models import ProductAlias
from assistant_linking.models import strip_leading_fragrantica_brand_name
from assistant_core.services.openai_responses import use_openai
from assistant_linking.services.ai_advisor import (
    create_fragrantica_rerank_recommendation,
)
from assistant_linking.services.ai_advisor import (
    latest_fragrantica_rerank_recommendation,
)
from assistant_linking.services.ai_recommendations import (
    learning_proposal_for_recommendation,
    sync_learning_proposal_for_recommendation,
)
from assistant_linking.services.parser_rules import get_regex_preprocess_rules
from assistant_linking.utils.text import fold_latin_diacritics
from assistant_linking.utils.text import normalize_alias_value
from assistant_linking.utils.text import normalize_mixed_script_latin_lookalikes
from catalog.models import Brand as CatalogBrand
from catalog.models import AIDraft as CatalogAIDraft
from catalog.models import FactClaim as CatalogFactClaim
from catalog.models import PerfumeAccord as CatalogPerfumeAccord
from catalog.models import Perfume as CatalogPerfume
from catalog.models import PerfumeNote as CatalogPerfumeNote
from catalog.models import Source as CatalogSource
from catalog.models import PerfumeVariant as CatalogPerfumeVariant
from catalog.models import get_or_create_collection
from prices import models
from prices.services.product_filters import (
    parse_exclude_terms,
    resolve_supplier_exclude_terms,
)
from prices.services.product_visibility import apply_hidden_product_keywords
from prices.services.job_queue import enqueue_management_command
from prices.services.catalog_formatting import normalize_catalogue_collection_name
from prices.services.catalog_formatting import normalize_catalogue_perfume_name
from prices.services.pagination import (
    CountlessPage,
    CountlessPaginator,
    paginate_queryset_without_count,
    parse_page_number,
)


logger = logging.getLogger(__name__)
FRAGRANTICA_REGEX_TIMEOUT_SECONDS = 1.0
FRAGRANTICA_REVIEW_STATUSES = {
    "all",
    "unlinked",
    "linked",
    "ignored",
}
OUR_PRODUCT_CATALOG_TABS = {
    "products",
    "brands",
    "collections",
    "concentrations",
    "audit",
}
CATALOGUE_LINKING_STATUSES = {"all", "unlinked", "linked"}
CATALOGUE_LINKING_SUGGESTION_FILTERS = {"all", "with", "without"}
CATALOGUE_LINKING_CONFIDENCE_FILTERS = {"all", "review", "0", "80", "90", "95", "100"}
CATALOGUE_LINKING_FILTER_SCAN_PAGES = 25
CATALOGUE_LINKING_FILTER_SCAN_MIN_BATCH = 200
CATALOGUE_LINKING_BULK_FILTERED_BATCH_SIZE = 100
CATALOGUE_LINKING_FILTER_CACHE_TTL_SECONDS = 600
CATALOGUE_LINKING_BROAD_ORDERING = (
    "brand_id",
    "collection_name",
    "name",
    "concentration",
    "id",
)
CATALOGUE_LINKING_SCOPED_ORDERING = (
    "brand__name",
    "collection_name",
    "name",
    "concentration",
)
CATALOGUE_CONCENTRATION_AUDIT_SCAN_PAGES = 25
CATALOGUE_CONCENTRATION_AUDIT_SCAN_MIN_BATCH = 200
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
    "extrait": "extrait de parfum",
    "parfum": "extrait de parfum",
    "perfume": "extrait de parfum",
    "pure perfume": "extrait de parfum",
    "edp": "eau de parfum",
    "edt": "eau de toilette",
    "edc": "eau de cologne",
}
FRAGRANCE_CONCENTRATION_NAME_TERMS = set(FRAGRANCE_CONCENTRATION_TERM_KEYS)
FRAGRANCE_CONCENTRATION_DISPLAY_LABELS = {
    "eau de parfum": "Eau de Parfum",
    "eau de toilette": "Eau de Toilette",
    "eau de cologne": "Eau de Cologne",
    "extrait de parfum": "Extrait de Parfum",
}
FRAGRANTICA_CONCENTRATION_MISMATCH_SCORE_CAP = 88
FRAGRANTICA_GENERIC_CONCENTRATION_WITH_EXPLICIT_SCORE = 98
CATALOGUE_LINKING_DEFAULT_MIN_SCORE = 80
CATALOGUE_CONCENTRATION_AUDIT_ISSUES = {
    "all",
    "source_conflict",
    "name_conflict",
    "name_contains_concentration",
    "source_unspecified_split",
    "multiple_local_concentrations",
}
CATALOGUE_CONCENTRATION_AUDIT_STATUSES = {
    "open",
    "all",
    CatalogPerfume.VERIFICATION_DRAFT,
    CatalogPerfume.VERIFICATION_REVIEW,
    CatalogPerfume.VERIFICATION_VERIFIED,
    CatalogPerfume.VERIFICATION_CONFLICT,
}


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
        return fragrantica_source_catalogue_display_name(self.source)

    @property
    def source_label(self) -> str:
        return f"{self.source.brand_name} / {self.source_display_name}"

    @property
    def reason_parts(self) -> list[str]:
        return catalogue_linking_reason_parts(self.reason)


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


@dataclass(frozen=True)
class CatalogueConcentrationAuditActionResult:
    level: str
    message: str
    redirect_url: str


@dataclass(frozen=True)
class CatalogueLinkingFilteredPageRows:
    rows: list[dict]
    known_count: int | None
    exhausted: bool


def catalogue_linking_reason_parts(reason: str) -> list[str]:
    reason = (reason or "").strip()
    if not reason:
        return []
    parts = [
        part.strip(" .")
        for part in re.split(r",|\band\b|;", reason, flags=re.IGNORECASE)
    ]
    return [part for part in parts if part]


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
    page_obj = list_context.get("page_obj")
    variants = list_context.get("variants", [])
    total_count = getattr(paginator, "count", None) if paginator else len(variants)
    total_count_display = (
        _countless_page_result_display(page_obj, noun="catalogue variants")
        if page_obj
        else f"{len(variants)} catalogue variants"
    )

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
    attach_linked_fragrantica_sources_to_variants(visible_variants)

    return {
        "variants": visible_variants,
        "total_count": total_count,
        "total_count_display": total_count_display,
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
    text = normalize_alias_value(
        fold_latin_diacritics(value or "").replace("&", " and ")
    )
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
    if not source_concentration_keys and perfume_concentration_key and score >= 98:
        return (
            score,
            "Exact brand and scent identity match; source concentration unspecified",
        )
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


def _audience_specific_base_keys_by_brand_id(
    brand_ids: set[int],
    *,
    perfume_manager=None,
) -> dict[int, set[str]]:
    if not brand_ids:
        return {}
    perfume_manager = perfume_manager or CatalogPerfume.objects
    queryset = perfume_manager.filter(brand_id__in=brand_ids)
    if hasattr(queryset, "only"):
        queryset = queryset.only("brand_id", "name", "audience")

    bases_by_brand_id: dict[int, set[str]] = defaultdict(set)
    for perfume in queryset:
        audience_group = audience_group_from_text(perfume.audience, perfume.name)
        if not audience_group:
            continue
        base_key = loose_fragrance_name_without_audience_or_concentration(perfume.name)
        if base_key:
            bases_by_brand_id[perfume.brand_id].add(base_key)
    return bases_by_brand_id


def _source_targets_audience_specific_catalogue_base(
    *,
    source_names: tuple[str, ...],
    source_audience_group: str,
    perfume,
    perfume_audience_group: str,
    audience_specific_bases_by_brand_id: dict[int, set[str]] | None,
) -> bool:
    if not source_audience_group or perfume_audience_group:
        return False
    if not audience_specific_bases_by_brand_id:
        return False
    perfume_base = loose_fragrance_name_without_audience_or_concentration(perfume.name)
    if not perfume_base:
        return False
    known_bases = audience_specific_bases_by_brand_id.get(perfume.brand_id, set())
    if perfume_base not in known_bases:
        return False
    source_bases = {
        loose_fragrance_name_without_audience_or_concentration(source_name)
        for source_name in source_names
    }
    source_bases.discard("")
    return perfume_base in source_bases


def fragrantica_source_is_available_for_perfume(
    source,
    perfume,
    *,
    allow_manual_extra_link: bool = False,
) -> bool:
    matched_perfume_id = getattr(source, "matched_perfume_id", None)
    if matched_perfume_id is None and getattr(source, "matched_perfume", None):
        matched_perfume_id = source.matched_perfume.id
    if not matched_perfume_id or matched_perfume_id == perfume.id:
        return True
    return (
        allow_manual_extra_link
        and getattr(source, "match_status", "") == FragranticaProduct.STATUS_LINKED
    )


def _manual_extra_fragrantica_link_reason(source, perfume) -> str:
    matched_perfume_id = getattr(source, "matched_perfume_id", None)
    if not matched_perfume_id or matched_perfume_id == perfume.id:
        return ""
    linked_name = (
        catalogue_linking_perfume_label(source.matched_perfume)
        if getattr(source, "matched_perfume", None)
        else "another Our Products row"
    )
    return (
        "Manual review: Fragrantica row is already linked to "
        f"{linked_name}. Add this second link only when both Our Products rows "
        "share the same external Fragrantica product."
    )


def _fragrantica_concentration_sort_rank(reason: str) -> int:
    if reason == "Exact brand, scent, and concentration match":
        return 0
    return 1


def _demote_generic_fragrantica_concentration_candidates(
    candidates_by_perfume: dict[int, dict[int, FragranticaMatchCandidate]],
    *,
    min_score: int,
) -> None:
    for perfume_id, candidates in candidates_by_perfume.items():
        has_explicit_concentration_match = any(
            candidate.score >= 100
            and candidate.reason == "Exact brand, scent, and concentration match"
            for candidate in candidates.values()
        )
        if not has_explicit_concentration_match:
            continue
        for source_id, candidate in list(candidates.items()):
            if (
                candidate.score < 100
                or candidate.reason
                != "Exact brand and scent identity match; source concentration unspecified"
            ):
                continue
            demoted_candidate = replace(
                candidate,
                score=FRAGRANTICA_GENERIC_CONCENTRATION_WITH_EXPLICIT_SCORE,
                reason=(
                    "Exact brand and scent identity match; source concentration "
                    "unspecified; concentration-specific source available"
                ),
            )
            if demoted_candidate.score < min_score:
                del candidates[source_id]
            else:
                candidates[source_id] = demoted_candidate


def _candidate_sort_key(candidate: FragranticaMatchCandidate):
    return (
        -candidate.score,
        _fragrantica_concentration_sort_rank(candidate.reason),
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
    manual_review_reason: str = "",
) -> None:
    existing = candidates.get(source.id)
    candidate = FragranticaMatchCandidate(
        source=source,
        match_type=match_type,
        score=score,
        reason=reason,
        creates_alias=creates_alias,
        manual_review_reason=manual_review_reason,
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
    brand_keys = fragrance_match_key_variants(perfume.brand.name)
    name_key = normalized_fragrance_key(perfume.name)
    name_base = fragrance_name_without_audience(perfume.name)
    audience_group = audience_group_from_text(perfume.audience, perfume.name)
    audience_specific_bases_by_brand_id = _audience_specific_base_keys_by_brand_id(
        {perfume.brand_id} if perfume.brand_id else set()
    )
    candidates: dict[int, FragranticaMatchCandidate] = {}

    for source in sources:
        if not (_fragrantica_source_brand_keys(source) & brand_keys):
            continue
        if not fragrantica_source_is_available_for_perfume(source, perfume):
            continue
        source_names = _fragrantica_source_match_names(source)
        source_audience_group = audience_group_from_text(source.audience, *source_names)
        if _source_targets_audience_specific_catalogue_base(
            source_names=source_names,
            source_audience_group=source_audience_group,
            perfume=perfume,
            perfume_audience_group=audience_group,
            audience_specific_bases_by_brand_id=audience_specific_bases_by_brand_id,
        ):
            continue
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
            if not (_fragrantica_source_brand_keys(source) & brand_keys):
                continue
            if not fragrantica_source_is_available_for_perfume(source, perfume):
                continue
            source_names = _fragrantica_source_match_names(source)
            source_name_keys = {normalized_fragrance_key(name) for name in source_names}
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
    variants = attach_linked_fragrantica_sources_to_variants(
        variants,
        fragrantica_manager=fragrantica_manager,
    )
    perfumes = {
        variant.perfume_id: variant.perfume
        for variant in variants
        if getattr(variant, "perfume_id", None)
        and getattr(variant, "perfume", None)
        and not getattr(variant, "fragrantica_linked_sources", [])
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
        if not getattr(variant, "fragrantica_linked_sources", []):
            variant.fragrantica_candidates = candidate_map.get(variant.perfume_id, [])
    return variants


def attach_linked_fragrantica_sources_to_variants(
    variants,
    *,
    fragrantica_manager=None,
) -> list:
    variants = list(variants)
    perfume_ids = [
        variant.perfume_id
        for variant in variants
        if getattr(variant, "perfume_id", None)
    ]
    linked_source_map = build_linked_fragrantica_sources_by_perfume_ids(
        perfume_ids,
        fragrantica_manager=fragrantica_manager,
    )
    for variant in variants:
        linked_sources = linked_source_map.get(
            getattr(variant, "perfume_id", None),
            [],
        )
        try:
            variant.fragrantica_linked_sources = linked_sources
            variant.fragrantica_candidates = []
        except AttributeError:
            continue
    return variants


def build_linked_fragrantica_sources_by_perfume_ids(
    perfume_ids,
    *,
    fragrantica_manager=None,
    link_manager=None,
) -> dict[int, list]:
    clean_ids = [int(perfume_id) for perfume_id in perfume_ids if perfume_id]
    if not clean_ids:
        return {}
    fragrantica_manager = fragrantica_manager or FragranticaProduct.objects
    link_manager = link_manager or FragranticaProductLink.objects
    linked_sources = (
        fragrantica_manager.filter(
            matched_perfume_id__in=clean_ids,
            match_status=FragranticaProduct.STATUS_LINKED,
        )
        .select_related("matched_perfume", "matched_perfume__brand")
        .order_by("brand_name", "collection_name", "name", "release_year", "id")
    )
    source_map: dict[int, list] = defaultdict(list)
    seen_source_ids_by_perfume_id: dict[int, set[int]] = defaultdict(set)
    for source in linked_sources:
        source.catalogue_display_name = fragrantica_source_catalogue_display_name(
            source
        )
        source.fragrantica_display_audience = source.audience or (
            source.matched_perfume.audience
            if getattr(source, "matched_perfume", None)
            else ""
        )
        source.review_link_type = FragranticaProductLink.LINK_TYPE_PRIMARY
        source_map[source.matched_perfume_id].append(source)
        seen_source_ids_by_perfume_id[source.matched_perfume_id].add(source.id)
    review_links = (
        link_manager.filter(perfume_id__in=clean_ids)
        .select_related(
            "perfume",
            "source",
            "source__matched_perfume",
            "source__matched_perfume__brand",
        )
        .order_by(
            "source__brand_name",
            "source__collection_name",
            "source__name",
            "source__release_year",
            "source_id",
        )
    )
    for link in review_links:
        source = link.source
        if source.id in seen_source_ids_by_perfume_id[link.perfume_id]:
            continue
        source.catalogue_display_name = fragrantica_source_catalogue_display_name(
            source
        )
        source.fragrantica_display_audience = source.audience or link.perfume.audience
        source.review_link_type = link.link_type
        source_map[link.perfume_id].append(source)
        seen_source_ids_by_perfume_id[link.perfume_id].add(source.id)
    return source_map


def fragrance_concentration_display_label(value: str) -> str:
    key = fragrance_concentration_identity_key(value)
    return FRAGRANCE_CONCENTRATION_DISPLAY_LABELS.get(key, value or "")


def fragrance_concentration_display_labels(values) -> tuple[str, ...]:
    labels = []
    for value in values:
        label = fragrance_concentration_display_label(value)
        if label and label not in labels:
            labels.append(label)
    return tuple(labels)


def normalize_catalogue_concentration_audit_issue(value: str | None) -> str:
    issue = (value or "all").strip() or "all"
    if issue not in CATALOGUE_CONCENTRATION_AUDIT_ISSUES:
        return "all"
    return issue


def normalize_catalogue_concentration_audit_status(value: str | None) -> str:
    status = (value or "open").strip() or "open"
    if status not in CATALOGUE_CONCENTRATION_AUDIT_STATUSES:
        return "open"
    return status


def _catalogue_concentration_audit_search_filter(query: str) -> Q:
    query = (query or "").strip()
    if not query:
        return Q()
    filters = Q()
    fields = (
        "brand__name",
        "name",
        "collection_name",
        "concentration",
        "audience",
    )
    tokens = [query, *catalog_search_tokens(query)]
    for token in dict.fromkeys(token for token in tokens if token):
        token_filter = Q()
        for field in fields:
            token_filter |= Q(**{f"{field}__icontains": token})
        filters &= token_filter
    return filters


def _catalogue_concentration_audit_base_key(perfume) -> tuple[int, str, str]:
    return (
        perfume.brand_id,
        loose_fragrance_name_without_audience_or_concentration(perfume.name),
        audience_group_from_text(perfume.audience, perfume.name),
    )


def _catalogue_concentration_group_summary(group_perfumes) -> tuple[str, ...]:
    keys = {
        fragrance_concentration_identity_key(perfume.concentration)
        for perfume in group_perfumes
        if fragrance_concentration_identity_key(perfume.concentration)
    }
    return fragrance_concentration_display_labels(sorted(keys))


def _catalogue_audit_source_concentration_keys(source_map, perfume_id: int) -> set[str]:
    keys: set[str] = set()
    for source in source_map.get(perfume_id, []):
        keys.update(
            fragrance_concentration_keys_from_text(
                fragrantica_source_catalogue_name(source),
                source.collection_name,
            )
        )
    return keys


def _catalogue_concentration_audit_row(
    perfume,
    *,
    group_perfumes,
    source_map,
) -> dict | None:
    local_key = fragrance_concentration_identity_key(perfume.concentration)
    name_keys = fragrance_concentration_keys_from_text(perfume.name)
    source_keys = _catalogue_audit_source_concentration_keys(source_map, perfume.id)
    group_concentrations = _catalogue_concentration_group_summary(group_perfumes)
    linked_sources = tuple(source_map.get(perfume.id, []))

    issue_type = ""
    severity = "medium"
    reason = ""
    if source_keys and local_key and local_key not in source_keys:
        issue_type = "source_conflict"
        severity = "high"
        reason = "Linked Fragrantica title contains a different concentration than this Our Products row."
    elif name_keys and local_key and local_key not in name_keys:
        issue_type = "name_conflict"
        severity = "high"
        reason = "Our Products scent name contains a concentration that differs from the concentration field."
    elif linked_sources and not source_keys and len(group_concentrations) > 1:
        issue_type = "source_unspecified_split"
        severity = "high"
        reason = "Linked Fragrantica source does not specify concentration, but this scent base has multiple local concentrations."
    elif name_keys:
        issue_type = "name_contains_concentration"
        severity = "medium"
        reason = "Our Products scent name contains concentration text; scent names should usually stay concentration-free."
    elif len(group_concentrations) > 1:
        issue_type = "multiple_local_concentrations"
        severity = "low"
        reason = "Same brand and scent base exists in multiple local concentrations; verify each concentration has external evidence."
    if not issue_type:
        return None

    return {
        "perfume": perfume,
        "issue_type": issue_type,
        "issue_label": issue_type.replace("_", " ").title(),
        "severity": severity,
        "reason": reason,
        "name_concentration_labels": fragrance_concentration_display_labels(
            sorted(name_keys)
        ),
        "source_concentration_labels": fragrance_concentration_display_labels(
            sorted(source_keys)
        ),
        "group_concentrations": group_concentrations,
        "linked_sources": linked_sources,
        "search_url": f"{reverse('prices:our_product_list')}?{urlencode({'q': perfume.name})}",
    }


def _catalogue_concentration_grouped_perfumes(perfumes, queryset) -> dict:
    perfumes = list(perfumes)
    base_keys = {
        _catalogue_concentration_audit_base_key(perfume) for perfume in perfumes
    }
    brand_ids = {
        getattr(perfume, "brand_id", None)
        for perfume in perfumes
        if getattr(perfume, "brand_id", None)
    }
    if not base_keys or not brand_ids:
        return defaultdict(list)

    grouped_perfumes: dict[tuple[int, str, str], list] = defaultdict(list)
    group_queryset = queryset.select_related(None).filter(brand_id__in=brand_ids)
    if hasattr(group_queryset, "only"):
        group_queryset = group_queryset.only(
            "id",
            "brand_id",
            "name",
            "concentration",
            "audience",
        )
    for perfume in group_queryset:
        key = _catalogue_concentration_audit_base_key(perfume)
        if key in base_keys:
            grouped_perfumes[key].append(perfume)
    return grouped_perfumes


def _attach_catalogue_concentration_audit_counts(perfumes) -> None:
    perfume_ids = [perfume.id for perfume in perfumes if getattr(perfume, "id", None)]
    if not perfume_ids:
        return
    variant_counts = {
        row["perfume_id"]: row["count"]
        for row in CatalogPerfumeVariant.objects.filter(perfume_id__in=perfume_ids)
        .values("perfume_id")
        .annotate(count=Count("id"))
    }
    supplier_counts = {
        row["catalog_perfume_id"]: row["count"]
        for row in models.SupplierProduct.objects.filter(
            catalog_perfume_id__in=perfume_ids,
        )
        .values("catalog_perfume_id")
        .annotate(count=Count("id"))
    }
    for perfume in perfumes:
        perfume_id = getattr(perfume, "id", None)
        if not perfume_id:
            continue
        perfume.variant_count = variant_counts.get(perfume_id, 0)
        perfume.linked_supplier_count = supplier_counts.get(perfume_id, 0)


def _catalogue_concentration_audit_rows_for_perfumes(
    perfumes,
    *,
    queryset,
    issue_filter: str,
) -> list[dict]:
    perfumes = list(perfumes)
    if not perfumes:
        return []
    source_map = build_linked_fragrantica_sources_by_perfume_ids(
        [perfume.id for perfume in perfumes]
    )
    grouped_perfumes = _catalogue_concentration_grouped_perfumes(perfumes, queryset)
    rows = []
    for perfume in perfumes:
        row = _catalogue_concentration_audit_row(
            perfume,
            group_perfumes=grouped_perfumes[
                _catalogue_concentration_audit_base_key(perfume)
            ],
            source_map=source_map,
        )
        if not row:
            continue
        if issue_filter != "all" and row["issue_type"] != issue_filter:
            continue
        rows.append(row)
    _attach_catalogue_concentration_audit_counts([row["perfume"] for row in rows])
    return rows


def _catalogue_concentration_audit_page(
    queryset,
    *,
    page_number,
    page_size: int,
    issue_filter: str,
):
    page_number = parse_page_number(page_number)
    target_start = max(page_number - 1, 0) * page_size
    target_stop = target_start + page_size + 1
    scan_size = max(page_size * 5, CATALOGUE_CONCENTRATION_AUDIT_SCAN_MIN_BATCH)
    scan_limit = max(
        page_size * CATALOGUE_CONCENTRATION_AUDIT_SCAN_PAGES,
        target_stop * CATALOGUE_CONCENTRATION_AUDIT_SCAN_PAGES,
    )
    matched_seen = 0
    page_rows = []

    for start in range(0, scan_limit, scan_size):
        batch = list(queryset[start : start + scan_size])
        if not batch:
            break
        batch_rows = _catalogue_concentration_audit_rows_for_perfumes(
            batch,
            queryset=queryset,
            issue_filter=issue_filter,
        )
        for row in batch_rows:
            if target_start <= matched_seen < target_stop:
                page_rows.append(row)
            matched_seen += 1
            if matched_seen >= target_stop:
                break
        if matched_seen >= target_stop:
            break

    has_next = len(page_rows) > page_size
    visible_rows = page_rows[:page_size]
    paginator = CountlessPaginator(
        per_page=page_size,
        current_page=page_number,
        has_next=has_next,
        object_list=queryset,
    )
    page_obj = CountlessPage(
        object_list=visible_rows,
        number=page_number,
        paginator=paginator,
        _has_next=has_next,
    )
    return paginator, page_obj, visible_rows, page_obj.has_other_pages()


def build_our_product_concentration_audit_context(
    request,
    *,
    page_size: int = 50,
    perfume_manager=None,
) -> dict:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    search_query = request.GET.get("q", "").strip()
    issue_filter = normalize_catalogue_concentration_audit_issue(
        request.GET.get("issue")
    )
    status_filter = normalize_catalogue_concentration_audit_status(
        request.GET.get("status")
    )
    queryset = perfume_manager.select_related("brand", "collection")
    if search_query:
        queryset = queryset.filter(
            _catalogue_concentration_audit_search_filter(search_query)
        )
    if status_filter == "open":
        queryset = queryset.exclude(
            verification_status=CatalogPerfume.VERIFICATION_VERIFIED
        )
    elif status_filter != "all":
        queryset = queryset.filter(verification_status=status_filter)
    queryset = queryset.order_by("brand__name", "name", "concentration", "id")
    paginator, page_obj, rows, is_paginated = _catalogue_concentration_audit_page(
        queryset,
        page_number=request.GET.get("page"),
        page_size=page_size,
        issue_filter=issue_filter,
    )
    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)
    return {
        "active_tab": "audit",
        "search_query": search_query,
        "issue_filter": issue_filter,
        "status_filter": status_filter,
        "audit_rows": rows,
        "audit_total_count": None,
        "audit_total_count_display": _countless_page_result_display(
            page_obj,
            noun="rows",
        ),
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": is_paginated,
        "query_string": query_without_page.urlencode(),
        "common_concentrations": tuple(FRAGRANCE_CONCENTRATION_DISPLAY_LABELS.values()),
    }


def run_our_product_concentration_audit_action(
    post_data,
    *,
    host: str = "",
    perfume_manager=None,
) -> CatalogueConcentrationAuditActionResult:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    redirect_url = post_data.get(
        "next",
        reverse("prices:our_product_concentration_audit"),
    )
    if not url_has_allowed_host_and_scheme(
        redirect_url,
        allowed_hosts={host} if host else None,
    ):
        redirect_url = reverse("prices:our_product_concentration_audit")

    perfume_id = post_data.get("perfume_id")
    if not str(perfume_id or "").isdigit():
        perfume = None
    else:
        perfume = first_from_queryset(
            perfume_manager.select_related("brand").filter(pk=perfume_id)
        )
    if not perfume:
        return CatalogueConcentrationAuditActionResult(
            "error",
            "Choose an Our Products row to review.",
            redirect_url,
        )

    action = post_data.get("action", "").strip()
    if action == "mark_reviewed":
        perfume.verification_status = CatalogPerfume.VERIFICATION_VERIFIED
        perfume.save(update_fields=["verification_status", "updated_at"])
        return CatalogueConcentrationAuditActionResult(
            "success",
            f"Marked reviewed: {perfume.brand.name} / {perfume.name}.",
            redirect_url,
        )
    if action == "mark_conflict":
        perfume.verification_status = CatalogPerfume.VERIFICATION_CONFLICT
        perfume.save(update_fields=["verification_status", "updated_at"])
        return CatalogueConcentrationAuditActionResult(
            "success",
            f"Marked concentration conflict: {perfume.brand.name} / {perfume.name}.",
            redirect_url,
        )
    if action == "set_concentration":
        raw_concentration = post_data.get("concentration", "").strip()
        concentration = fragrance_concentration_display_label(
            raw_concentration
        ) or normalize_catalogue_perfume_name(raw_concentration)
        if not concentration:
            return CatalogueConcentrationAuditActionResult(
                "error",
                "Concentration is required.",
                redirect_url,
            )
        perfume.concentration = concentration
        perfume.verification_status = CatalogPerfume.VERIFICATION_REVIEW
        perfume.save(
            update_fields=["concentration", "verification_status", "updated_at"]
        )
        return CatalogueConcentrationAuditActionResult(
            "success",
            f"Updated concentration for {perfume.brand.name} / {perfume.name}.",
            redirect_url,
        )

    return CatalogueConcentrationAuditActionResult(
        "error",
        "Unknown concentration audit action.",
        redirect_url,
    )


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
    brands = base_queryset.order_by("brand_name").values("brand_name").distinct()

    filtered_queryset = base_queryset
    if selected_brand:
        filtered_queryset = filtered_queryset.filter(brand_name__iexact=selected_brand)
    if search_query:
        filtered_queryset = filtered_queryset.filter(
            fragrantica_product_search_filter(search_query)
        )

    status_counts = {}
    if status_filter != "all":
        filtered_queryset = filtered_queryset.filter(match_status=status_filter)

    queryset = filtered_queryset.order_by(
        "brand_name",
        "collection_name",
        "name",
        "audience",
        "release_year",
        "id",
    )
    if paginator_class is Paginator:
        paginator, page_obj, source_rows, is_paginated = (
            paginate_queryset_without_count(
                queryset,
                page_number=request.GET.get("page"),
                page_size=page_size,
            )
        )
    else:
        paginator = paginator_class(queryset, page_size)
        page_obj = paginator.get_page(request.GET.get("page"))
        source_rows = list(page_obj.object_list)
        is_paginated = getattr(paginator, "num_pages", 1) > 1

    has_next = page_obj.has_next() if hasattr(page_obj, "has_next") else is_paginated
    end_index = (
        page_obj.end_index() if hasattr(page_obj, "end_index") else len(source_rows)
    )
    filtered_count_display = f"{end_index}+" if has_next else str(end_index)
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
                "display_name": fragrantica_source_catalogue_display_name(row),
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
        "total_count": None,
        "filtered_count": end_index,
        "filtered_count_display": filtered_count_display,
        "rows": rows,
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": is_paginated,
        "query_string": query_without_page.urlencode(),
    }


def fragrantica_identity_key(brand_name: str, perfume_name: str) -> tuple[str, str]:
    return (
        normalize_alias_value(fold_latin_diacritics(brand_name or "")).replace(
            "&",
            "and",
        ),
        normalize_alias_value(fold_latin_diacritics(perfume_name or "")).replace(
            "&",
            "and",
        ),
    )


def fragrantica_source_catalogue_name(source) -> str:
    return strip_leading_fragrantica_brand_name(
        getattr(source, "brand_name", ""),
        getattr(source, "name", ""),
    )


def fragrantica_source_catalogue_display_name(source) -> str:
    return normalize_catalogue_perfume_name(fragrantica_source_catalogue_name(source))


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


def _fragrantica_source_brand_keys(
    source,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> set[str]:
    return {
        key
        for key in fragrance_match_key_variants(
            getattr(source, "brand_name", ""),
            getattr(source, "normalized_brand_name", ""),
            regex_preprocess_rules=regex_preprocess_rules,
        )
        if key
    }


def _build_fragrantica_source_brand_id_map(
    sources,
    *,
    brand_manager=None,
    brand_alias_manager=None,
    regex_preprocess_rules: tuple[tuple[str, str], ...] = (),
) -> dict[str, set[int]]:
    source_keys = set()
    for source in sources:
        source_keys.update(
            _fragrantica_source_brand_keys(
                source,
                regex_preprocess_rules=regex_preprocess_rules,
            )
        )
    if not source_keys:
        return {}

    brand_key_to_ids: dict[str, set[int]] = defaultdict(set)
    brand_manager = brand_manager or CatalogBrand.objects
    brands = brand_manager.all()
    if hasattr(brands, "only"):
        brands = brands.only("id", "name")
    for brand in brands:
        matching_keys = source_keys & fragrance_match_key_variants(
            getattr(brand, "name", ""),
            regex_preprocess_rules=regex_preprocess_rules,
        )
        for key in matching_keys:
            brand_key_to_ids[key].add(brand.id)

    brand_alias_manager = brand_alias_manager or BrandAlias.objects
    for alias in brand_alias_manager.filter(active=True):
        matching_keys = source_keys & fragrance_match_key_variants(
            alias.normalized_alias or alias.alias_text,
            alias.alias_text,
            regex_preprocess_rules=regex_preprocess_rules,
        )
        for key in matching_keys:
            brand_key_to_ids[key].add(alias.brand_id)
    return brand_key_to_ids


def _group_perfumes_by_brand_id(perfumes) -> dict[int, list]:
    grouped: dict[int, list] = defaultdict(list)
    for perfume in perfumes:
        brand_id = getattr(perfume, "brand_id", None)
        if brand_id:
            grouped[brand_id].append(perfume)
    return grouped


def _perfumes_for_brand_ids(perfumes_by_brand_id: dict[int, list], brand_ids: set[int]):
    for brand_id in sorted(brand_ids):
        yield from perfumes_by_brand_id.get(brand_id, [])


def _fragrantica_normalized_name_candidates(
    *,
    brand_name: str,
    name: str,
    collection_name: str = "",
    concentration: str = "",
) -> set[str]:
    names = {name or ""}
    if collection_name:
        names.add(f"{collection_name} {name}")

    concentration_key = fragrance_concentration_identity_key(concentration)
    concentration_terms = {
        term
        for term, canonical_key in FRAGRANCE_CONCENTRATION_TERM_KEYS.items()
        if concentration_key and canonical_key == concentration_key
    }
    expanded_names = set(names)
    for name in names:
        for term in concentration_terms:
            expanded_names.add(f"{name} {term}")

    normalized_names: set[str] = set()
    for name in expanded_names:
        if not name:
            continue
        normalized_names.add(normalized_fragrantica_product_name(brand_name, name))
        normalized_names.add(normalized_fragrance_key(name))
    normalized_names.discard("")
    return normalized_names


def _fragrantica_normalized_name_candidates_for_perfume(perfume) -> set[str]:
    return _fragrantica_normalized_name_candidates(
        brand_name=getattr(getattr(perfume, "brand", None), "name", ""),
        name=getattr(perfume, "name", ""),
        collection_name=getattr(perfume, "collection_name", "") or "",
        concentration=getattr(perfume, "concentration", ""),
    )


def _fragrantica_normalized_name_candidates_for_alias(
    alias,
    *,
    brand_name: str,
    concentration: str = "",
) -> set[str]:
    return _fragrantica_normalized_name_candidates(
        brand_name=brand_name,
        name=getattr(alias, "alias_text", "") or "",
        concentration=concentration,
    ) | _fragrantica_normalized_name_candidates(
        brand_name=brand_name,
        name=getattr(alias, "canonical_text", "") or "",
        concentration=concentration,
    )


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
    audience_specific_bases_by_brand_id: dict[int, set[str]] | None = None,
) -> tuple[int, str]:
    source_names = _fragrantica_source_match_names(source)
    source_audience_group = audience_group_from_text(source.audience, *source_names)
    perfume_audience_group = audience_group_from_text(perfume.audience, perfume.name)
    if _source_targets_audience_specific_catalogue_base(
        source_names=source_names,
        source_audience_group=source_audience_group,
        perfume=perfume,
        perfume_audience_group=perfume_audience_group,
        audience_specific_bases_by_brand_id=audience_specific_bases_by_brand_id,
    ):
        return 0, ""
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
        _fragrantica_concentration_sort_rank(_reason),
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
    brand_manager=None,
    brand_alias_manager=None,
    product_alias_manager=None,
    limit: int = 4,
) -> dict:
    provided_perfume_manager = perfume_manager is not None
    perfume_manager = perfume_manager or CatalogPerfume.objects
    product_alias_manager = product_alias_manager or ProductAlias.objects
    fragrantica_rows = list(fragrantica_rows)
    if not fragrantica_rows:
        return {}
    candidates = perfume_manager.select_related("brand").filter(name__isnull=False)
    candidates = candidates.filter(brand__name__isnull=False)
    if not provided_perfume_manager:
        regex_preprocess_rules = get_regex_preprocess_rules()
        source_brand_key_to_ids = _build_fragrantica_source_brand_id_map(
            fragrantica_rows,
            brand_manager=brand_manager,
            brand_alias_manager=brand_alias_manager,
            regex_preprocess_rules=regex_preprocess_rules,
        )
        source_brand_ids = {
            brand_id
            for brand_ids in source_brand_key_to_ids.values()
            for brand_id in brand_ids
        }
        if not source_brand_ids:
            return {}
        candidates = candidates.filter(brand_id__in=source_brand_ids)
    perfumes = list(candidates)
    if not perfumes:
        return {}
    if provided_perfume_manager:
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

    perfumes_by_brand_id = _group_perfumes_by_brand_id(perfumes)
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
        for perfume in _perfumes_for_brand_ids(perfumes_by_brand_id, source_brand_ids):
            if not fragrantica_source_is_available_for_perfume(
                source,
                perfume,
                allow_manual_extra_link=True,
            ):
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
    brand_ids = {perfume.brand_id for perfume in perfumes if perfume.brand_id}
    audience_specific_bases_by_brand_id = _audience_specific_base_keys_by_brand_id(
        brand_ids
    )

    source_queryset = (
        fragrantica_manager.select_related("matched_perfume", "collection")
        .filter(normalized_brand_name__in=brand_keys)
        .exclude(match_status=FragranticaProduct.STATUS_IGNORED)
    )
    aliases = list(
        product_alias_manager.filter(active=True)
        .filter(Q(brand_id__in=brand_ids) | Q(brand__isnull=True))
        .order_by("priority", "alias_text")
    )
    source_name_candidates_by_perfume_id: dict[int, set[str]] = {}
    if min_score >= 95:
        source_name_candidates = set()
        for perfume in perfumes:
            perfume_candidates = _fragrantica_normalized_name_candidates_for_perfume(
                perfume
            )
            source_name_candidates_by_perfume_id[perfume.id] = set(perfume_candidates)
            source_name_candidates.update(perfume_candidates)
        perfumes_by_id = {perfume.id: perfume for perfume in perfumes}
        perfumes_by_brand_id = _group_perfumes_by_brand_id(perfumes)
        for alias in aliases:
            alias_brand_id = getattr(alias, "brand_id", None)
            alias_perfume_id = getattr(alias, "perfume_id", None)
            if alias_perfume_id:
                alias_perfume = perfumes_by_id.get(alias_perfume_id)
                alias_perfumes = [alias_perfume] if alias_perfume else []
            elif alias_brand_id:
                alias_perfumes = perfumes_by_brand_id.get(alias_brand_id, [])
            else:
                alias_perfumes = perfumes
            for perfume in alias_perfumes:
                alias_candidates = _fragrantica_normalized_name_candidates_for_alias(
                    alias,
                    brand_name=perfume.brand.name,
                    concentration=perfume.concentration,
                )
                source_name_candidates_by_perfume_id.setdefault(
                    perfume.id,
                    set(),
                ).update(alias_candidates)
                source_name_candidates.update(alias_candidates)
        if source_name_candidates:
            source_queryset = source_queryset.filter(
                normalized_name__in=source_name_candidates
            )
    source_rows = list(
        source_queryset.order_by(
            "match_status",
            "brand_name",
            "collection_name",
            "name",
            "id",
        )
    )
    aliases_by_brand_id: dict[int | None, list[ProductAlias]] = defaultdict(list)
    for alias in aliases:
        aliases_by_brand_id[getattr(alias, "brand_id", None)].append(alias)

    perfumes_by_brand_id = _group_perfumes_by_brand_id(perfumes)
    candidates_by_perfume: dict[int, dict[int, FragranticaMatchCandidate]] = {
        perfume.id: {} for perfume in perfumes
    }
    for source in source_rows:
        source_name_key = source.normalized_name or normalized_fragrance_key(
            strip_leading_fragrantica_brand_name(source.brand_name, source.name)
        )
        source_brand_ids = _source_brand_ids(
            source,
            brand_key_to_ids,
            regex_preprocess_rules=regex_preprocess_rules,
        )
        if not source_brand_ids:
            continue
        for perfume in _perfumes_for_brand_ids(perfumes_by_brand_id, source_brand_ids):
            if not fragrantica_source_is_available_for_perfume(
                source,
                perfume,
                allow_manual_extra_link=True,
            ):
                continue
            if (
                min_score >= 95
                and source_name_key
                not in source_name_candidates_by_perfume_id.get(perfume.id, set())
            ):
                continue
            candidate_aliases = aliases_by_brand_id.get(
                perfume.brand_id, []
            ) + aliases_by_brand_id.get(None, [])
            score, reason = _fragrantica_perfume_candidate_score(
                source,
                perfume,
                candidate_aliases,
                regex_preprocess_rules=regex_preprocess_rules,
                audience_specific_bases_by_brand_id=(
                    audience_specific_bases_by_brand_id
                ),
            )
            if score <= 0 or score < min_score:
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
                manual_review_reason=_manual_extra_fragrantica_link_reason(
                    source,
                    perfume,
                ),
            )

    _demote_generic_fragrantica_concentration_candidates(
        candidates_by_perfume,
        min_score=min_score,
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
            current = candidates_by_perfume.get(perfume.id, {}).get(candidate.source.id)
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
        fragrantica_source_catalogue_display_name(source),
        source.audience,
        str(source.release_year) if source.release_year else "",
    ]
    return " / ".join(part for part in parts if part)


def serialize_catalogue_linking_source(source) -> dict:
    display_audience = getattr(source, "fragrantica_display_audience", "") or (
        source.audience
        or (
            source.matched_perfume.audience
            if getattr(source, "matched_perfume", None)
            else ""
        )
    )
    return {
        "source_id": source.id,
        "label": catalogue_linking_source_label(source),
        "brand": source.brand_name,
        "name": fragrantica_source_catalogue_display_name(source),
        "collection": source.collection_name,
        "audience": display_audience,
        "release_year": source.release_year,
        "match_status": source.match_status,
        "source_href": source.source_href,
        "unlink_url": reverse("prices:fragrantica_product_unlink", args=[source.id]),
        "link_type": getattr(source, "review_link_type", ""),
        "review_url": (
            f"{reverse('prices:fragrantica_product_review')}?"
            f"{urlencode({'q': fragrantica_source_catalogue_name(source)})}"
        ),
    }


def serialize_catalogue_linking_candidate(candidate: FragranticaMatchCandidate) -> dict:
    source = candidate.source
    can_link = source.match_status != FragranticaProduct.STATUS_LINKED or bool(
        candidate.manual_review_reason
    )
    serialized = serialize_catalogue_linking_source(source)
    serialized.update(
        {
            "score": candidate.score,
            "reason": candidate.reason,
            "reason_parts": candidate.reason_parts,
            "match_type": candidate.match_type,
            "creates_alias": candidate.creates_alias,
            "manual_review_reason": candidate.manual_review_reason,
            "manual_review_link": bool(candidate.manual_review_reason),
            "can_link": can_link,
            "link_url": reverse("prices:fragrantica_product_link", args=[source.pk]),
        }
    )
    return serialized


def serialize_catalogue_linking_manual_source(source, perfume) -> dict:
    linked_to_selected = (
        source.match_status == FragranticaProduct.STATUS_LINKED
        and source.matched_perfume_id == perfume.id
    )
    linked_elsewhere = (
        source.match_status == FragranticaProduct.STATUS_LINKED
        and source.matched_perfume_id
        and source.matched_perfume_id != perfume.id
    )
    manual_review_reason = ""
    if linked_elsewhere:
        linked_name = (
            catalogue_linking_perfume_label(source.matched_perfume)
            if getattr(source, "matched_perfume", None)
            else "another Our Products row"
        )
        manual_review_reason = f"Manual review: already linked to {linked_name}"
    source_name = display_name_without_concentration(
        fragrantica_source_catalogue_name(source)
    )
    serialized = serialize_catalogue_linking_source(source)
    serialized.update(
        {
            "score": None,
            "reason": "Manual Fragrantica search result",
            "reason_parts": ["Manual search result"],
            "match_type": "manual_search",
            "creates_alias": normalized_fragrance_key(source_name)
            != normalized_fragrance_key(perfume.name),
            "manual_review_reason": manual_review_reason,
            "manual_review_link": bool(manual_review_reason),
            "can_link": not linked_to_selected,
            "link_url": reverse("prices:fragrantica_product_link", args=[source.pk]),
        }
    )
    return serialized


def serialize_catalogue_linking_ai_advice(recommendation) -> dict:
    payload = recommendation.recommendation_json or {}
    source = recommendation.fragrantica_product
    proposal = learning_proposal_for_recommendation(recommendation)
    return {
        "id": recommendation.id,
        "status": recommendation.status,
        "model_name": recommendation.model_name,
        "confidence": recommendation.confidence,
        "risk_level": recommendation.risk_level,
        "reasoning": recommendation.reasoning or payload.get("reasoning", ""),
        "recommended_candidate_id": payload.get("recommended_candidate_id"),
        "review_url": reverse(
            "prices:catalogue_linking_ai_advice_review",
            args=[recommendation.id],
        ),
        "can_review": recommendation.status == AIRecommendation.STATUS_PENDING,
        "learning_proposal": (
            {
                "id": proposal.id,
                "status": proposal.status,
                "label": proposal.get_status_display(),
                "title": proposal.title,
            }
            if proposal
            else None
        ),
        "recommended_label": (
            f"{source.brand_name} / {fragrantica_source_catalogue_name(source)}"
            if source
            else ""
        ),
        "candidate_notes": payload.get("candidate_notes", []),
    }


def serialize_catalogue_linking_selected_perfume(perfume) -> dict:
    return {
        "id": perfume.id,
        "label": catalogue_linking_perfume_label(perfume),
        "brand": perfume.brand.name,
        "name": perfume.name,
        "collection": perfume.collection_name,
        "concentration": perfume.concentration,
        "audience": perfume.audience,
        "release_year": perfume.release_year,
    }


def catalogue_linking_row_payload_json(row: dict) -> str:
    latest_advice = None
    perfume = row["perfume"]
    candidates = row["candidates"]
    if candidates:
        latest_advice = latest_fragrantica_rerank_recommendation(
            perfume=perfume,
            candidates=candidates,
        )
    payload = {
        "selected": serialize_catalogue_linking_selected_perfume(perfume),
        "linked_sources": [
            serialize_catalogue_linking_source(source)
            for source in row["linked_sources"]
        ],
        "candidates": [
            serialize_catalogue_linking_candidate(candidate) for candidate in candidates
        ],
        "ai_advice": (
            serialize_catalogue_linking_ai_advice(latest_advice)
            if latest_advice
            else None
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_fragrantica_catalogue_link_response_payload(
    result: FragranticaCatalogueLinkResult,
    *,
    source_id,
    perfume_id,
    source_manager=None,
    perfume_manager=None,
) -> dict:
    payload = {
        "ok": result.level != "error",
        "level": result.level,
        "message": result.message,
        "redirect_url": result.redirect_url,
    }
    if not source_id or not perfume_id:
        return payload

    source_manager = source_manager or FragranticaProduct.objects
    perfume_manager = perfume_manager or CatalogPerfume.objects
    try:
        source = source_manager.get(pk=source_id)
        perfume = perfume_manager.select_related("brand").get(pk=perfume_id)
    except (FragranticaProduct.DoesNotExist, CatalogPerfume.DoesNotExist, ValueError):
        return payload

    source.fragrantica_display_audience = source.audience or perfume.audience
    payload.update(
        {
            "selected": serialize_catalogue_linking_selected_perfume(perfume),
            "linked_source": serialize_catalogue_linking_source(source),
        }
    )
    return payload


def build_fragrantica_catalogue_unlink_response_payload(
    result: FragranticaCatalogueLinkResult,
    *,
    perfume_id,
    perfume_manager=None,
) -> dict:
    payload = {
        "ok": result.level != "error",
        "level": result.level,
        "message": result.message,
        "redirect_url": result.redirect_url,
    }
    if not perfume_id:
        return payload

    perfume_manager = perfume_manager or CatalogPerfume.objects
    try:
        perfume = perfume_manager.select_related("brand").get(pk=perfume_id)
    except (CatalogPerfume.DoesNotExist, ValueError):
        return payload

    linked_sources = build_linked_fragrantica_sources_by_perfume_ids([perfume.id]).get(
        perfume.id,
        [],
    )
    payload.update(
        {
            "selected": serialize_catalogue_linking_selected_perfume(perfume),
            "linked_sources": [
                serialize_catalogue_linking_source(source) for source in linked_sources
            ],
        }
    )
    return payload


def build_catalogue_linking_perfume_queryset(
    request,
    *,
    perfume_manager=None,
):
    perfume_manager = perfume_manager or CatalogPerfume.objects
    selected_brand = normalize_fragrantica_review_brand_id(
        request.GET.get("brand") or ""
    )
    search_query = request.GET.get("q", "").strip()
    status_filter = normalize_catalogue_linking_status(request.GET.get("status"))

    queryset = _apply_catalogue_linking_perfume_filters(
        perfume_manager.select_related("brand", "collection"),
        selected_brand=selected_brand,
        search_query=search_query,
    )
    queryset = _filter_catalogue_linking_queryset_by_link_status(
        queryset,
        status_filter,
    )
    if _catalogue_linking_request_uses_high_confidence_prefilter(request):
        exact_queryset = _apply_catalogue_linking_perfume_filters(
            perfume_manager.select_related("brand"),
            selected_brand=selected_brand,
            search_query=search_query,
        )
        exact_queryset = _filter_catalogue_linking_queryset_by_link_status(
            exact_queryset,
            status_filter,
        )
        exact_perfume_ids = _catalogue_linking_strict_exact_perfume_ids(
            exact_queryset,
            include_linked_sources=_catalogue_linking_request_uses_review_filter(
                request,
            ),
        )
        queryset = queryset.filter(pk__in=exact_perfume_ids)
    ordering = (
        CATALOGUE_LINKING_BROAD_ORDERING
        if _catalogue_linking_request_uses_broad_default_listing(
            request,
            selected_brand=selected_brand,
            search_query=search_query,
            status_filter=status_filter,
        )
        else CATALOGUE_LINKING_SCOPED_ORDERING
    )
    return queryset.order_by(*ordering)


def _catalogue_linking_request_uses_broad_default_listing(
    request,
    *,
    selected_brand: str,
    search_query: str,
    status_filter: str,
) -> bool:
    confidence_filter = normalize_catalogue_linking_confidence_filter(
        request.GET.get("confidence")
    )
    suggestion_filter = normalize_catalogue_linking_suggestion_filter(
        request.GET.get("suggestions")
    )
    return (
        not selected_brand
        and not search_query
        and status_filter == "all"
        and confidence_filter == "all"
        and suggestion_filter == "all"
    )


def catalogue_linking_request_uses_scored_row_filter(request) -> bool:
    confidence_filter = normalize_catalogue_linking_confidence_filter(
        request.GET.get("confidence")
    )
    suggestion_filter = normalize_catalogue_linking_suggestion_filter(
        request.GET.get("suggestions")
    )
    return suggestion_filter != "all" or confidence_filter != "all"


def _catalogue_linking_filter_cache_key(
    request,
    *,
    page_size: int,
    min_score: int,
) -> str:
    session_key = getattr(getattr(request, "session", None), "session_key", "") or ""
    if not session_key:
        session_key = "anonymous"
    payload = {
        "v": 3,
        "session": session_key,
        "brand": normalize_fragrantica_review_brand_id(request.GET.get("brand") or ""),
        "q": request.GET.get("q", "").strip(),
        "status": normalize_catalogue_linking_status(request.GET.get("status")),
        "suggestions": normalize_catalogue_linking_suggestion_filter(
            request.GET.get("suggestions")
        ),
        "confidence": normalize_catalogue_linking_confidence_filter(
            request.GET.get("confidence")
        ),
        "min_score": min_score,
        "page_size": page_size,
    }
    digest = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"catalogue-linking-filter:{digest}"


def _apply_catalogue_linking_perfume_filters(
    queryset,
    *,
    selected_brand: str,
    search_query: str,
):
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
    return queryset


def _filter_catalogue_linking_queryset_by_link_status(queryset, status_filter: str):
    if status_filter not in {"linked", "unlinked"}:
        return queryset
    queryset = queryset.alias(
        _has_linked_fragrantica_source=Exists(
            FragranticaProduct.objects.filter(
                matched_perfume_id=OuterRef("pk"),
                match_status=FragranticaProduct.STATUS_LINKED,
            )
        ),
        _has_reviewed_fragrantica_link=Exists(
            FragranticaProductLink.objects.filter(perfume_id=OuterRef("pk"))
        ),
    )
    linked_filter = Q(_has_linked_fragrantica_source=True) | Q(
        _has_reviewed_fragrantica_link=True
    )
    if status_filter == "linked":
        return queryset.filter(linked_filter)
    return queryset.filter(
        _has_linked_fragrantica_source=False,
        _has_reviewed_fragrantica_link=False,
    )


def _attach_catalogue_linking_row_counts(perfumes) -> None:
    perfume_ids = [perfume.id for perfume in perfumes if getattr(perfume, "id", None)]
    if not perfume_ids:
        return

    variant_counts = {
        row["perfume_id"]: row["count"]
        for row in CatalogPerfumeVariant.objects.filter(perfume_id__in=perfume_ids)
        .values("perfume_id")
        .annotate(count=Count("id"))
    }
    linked_source_counts = {
        row["matched_perfume_id"]: row["count"]
        for row in FragranticaProduct.objects.filter(
            matched_perfume_id__in=perfume_ids,
            match_status=FragranticaProduct.STATUS_LINKED,
        )
        .values("matched_perfume_id")
        .annotate(count=Count("id"))
    }
    reviewed_link_counts = {
        row["perfume_id"]: row["count"]
        for row in FragranticaProductLink.objects.filter(perfume_id__in=perfume_ids)
        .values("perfume_id")
        .annotate(count=Count("id"))
    }
    for perfume in perfumes:
        perfume_id = getattr(perfume, "id", None)
        if not perfume_id:
            continue
        perfume.variant_count = variant_counts.get(perfume_id, 0)
        perfume.linked_fragrantica_count = linked_source_counts.get(
            perfume_id,
            0,
        ) + reviewed_link_counts.get(perfume_id, 0)
        perfume.reviewed_fragrantica_link_count = reviewed_link_counts.get(
            perfume_id,
            0,
        )


def build_catalogue_linking_rows(
    perfumes,
    *,
    min_score: int,
    include_candidates: bool = True,
    include_payload: bool = True,
) -> list[dict]:
    perfumes = list(perfumes)
    _attach_catalogue_linking_row_counts(perfumes)
    linked_source_map = build_linked_fragrantica_sources_by_perfume_ids(
        [perfume.id for perfume in perfumes]
    )
    candidate_map = (
        build_catalogue_fragrantica_candidates_for_perfumes(
            perfumes,
            min_score=min_score,
            limit=5,
        )
        if include_candidates
        else {}
    )
    rows = []
    for perfume in perfumes:
        linked_sources = linked_source_map.get(perfume.id, [])
        candidates = [] if linked_sources else candidate_map.get(perfume.id, [])
        top_candidate = candidates[0] if candidates else None
        candidates_deferred = not include_candidates and not linked_sources
        row = {
            "perfume": perfume,
            "label": catalogue_linking_perfume_label(perfume),
            "linked_sources": linked_sources,
            "top_linked_source": linked_sources[0] if linked_sources else None,
            "candidates": candidates,
            "candidates_deferred": candidates_deferred,
            "top_candidate": top_candidate,
            "ready_for_bulk": bool(
                top_candidate
                and top_candidate.score >= min_score
                and not top_candidate.manual_review_reason
                and top_candidate.source.match_status
                != FragranticaProduct.STATUS_LINKED
            ),
        }
        row["payload_json"] = (
            catalogue_linking_row_payload_json(row)
            if include_payload and (include_candidates or linked_sources)
            else ""
        )
        rows.append(row)
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
    if confidence_filter == "review":
        return [
            row
            for row in rows
            if row["top_candidate"] and row["top_candidate"].manual_review_reason
        ]
    min_score = normalize_catalogue_linking_min_score(confidence_filter)
    return [
        row
        for row in rows
        if row["top_candidate"]
        and row["top_candidate"].score >= min_score
        and not row["top_candidate"].manual_review_reason
    ]


def _catalogue_linking_sequence_count(sequence) -> int:
    count = getattr(sequence, "count", None)
    if callable(count) and not isinstance(sequence, (list, tuple)):
        return count()
    return len(sequence)


def _catalogue_linking_sequence_slice(sequence, start: int, stop: int):
    return sequence[start:stop]


def _countless_page_result_display(page_obj, *, noun: str) -> str:
    if not page_obj:
        return f"0 {noun}"
    paginator = getattr(page_obj, "paginator", None)
    count = getattr(paginator, "count", None)
    if count is not None:
        return f"{count} {noun}"
    end_index = page_obj.end_index() if hasattr(page_obj, "end_index") else 0
    has_next = page_obj.has_next() if hasattr(page_obj, "has_next") else False
    suffix = "+" if has_next else ""
    return f"{end_index}{suffix} {noun}"


def _countless_visible_count_display(page_obj, visible_count) -> str:
    if visible_count is not None:
        return str(visible_count)
    return ""


def _catalogue_linking_lightweight_perfume_rows(sequence) -> list[dict]:
    query = getattr(sequence, "query", None)
    if getattr(sequence, "model", None) is CatalogPerfume and getattr(
        query, "annotations", {}
    ):
        sequence = CatalogPerfume.objects.filter(
            pk__in=Subquery(sequence.values("pk"))
        ).select_related("brand")
    if hasattr(sequence, "values"):
        return list(
            sequence.values(
                "id",
                "brand_id",
                "brand__name",
                "name",
                "collection_name",
                "concentration",
            )
        )
    return [
        {
            "id": getattr(perfume, "id", None),
            "brand_id": getattr(perfume, "brand_id", None),
            "brand__name": getattr(getattr(perfume, "brand", None), "name", ""),
            "name": getattr(perfume, "name", ""),
            "collection_name": getattr(perfume, "collection_name", ""),
            "concentration": getattr(perfume, "concentration", ""),
        }
        for perfume in sequence
    ]


def _catalogue_linking_strict_exact_perfume_ids(
    sequence,
    *,
    include_linked_sources: bool = False,
) -> set[int]:
    perfume_rows = _catalogue_linking_lightweight_perfume_rows(sequence)
    if not perfume_rows:
        return set()

    regex_preprocess_rules = get_regex_preprocess_rules()
    brand_key_to_ids: dict[str, set[int]] = defaultdict(set)
    brand_ids = set()
    for row in perfume_rows:
        brand_id = row.get("brand_id")
        brand_name = row.get("brand__name") or ""
        if not brand_id or not brand_name:
            continue
        brand_ids.add(brand_id)
        for key in fragrance_match_key_variants(
            brand_name,
            regex_preprocess_rules=regex_preprocess_rules,
        ):
            brand_key_to_ids[key].add(brand_id)
    for alias in BrandAlias.objects.filter(active=True, brand_id__in=brand_ids):
        for key in fragrance_match_key_variants(
            alias.normalized_alias or alias.alias_text,
            alias.alias_text,
            regex_preprocess_rules=regex_preprocess_rules,
        ):
            brand_key_to_ids[key].add(alias.brand_id)
    brand_keys = set(brand_key_to_ids)
    if not brand_keys:
        return set()

    perfume_ids_by_brand_name: dict[tuple[int, str], set[int]] = defaultdict(set)
    for row in perfume_rows:
        brand_id = row.get("brand_id")
        if not brand_id:
            continue
        perfume_id = row["id"]
        for normalized_name in _fragrantica_normalized_name_candidates(
            brand_name=row.get("brand__name") or "",
            name=row.get("name") or "",
            collection_name=row.get("collection_name") or "",
            concentration=row.get("concentration") or "",
        ):
            perfume_ids_by_brand_name[(brand_id, normalized_name)].add(perfume_id)

    matched_perfume_ids: set[int] = set()
    source_queryset = FragranticaProduct.objects.filter(
        normalized_brand_name__in=brand_keys,
    )
    if include_linked_sources:
        source_queryset = source_queryset.exclude(
            match_status=FragranticaProduct.STATUS_IGNORED,
        )
    else:
        source_queryset = source_queryset.filter(
            match_status=FragranticaProduct.STATUS_UNLINKED,
        )
    source_rows = source_queryset.values_list(
        "brand_name",
        "normalized_brand_name",
        "normalized_name",
        "name",
    ).iterator(chunk_size=1000)
    for (
        source_brand_name,
        source_normalized_brand_name,
        source_normalized_name,
        source_name,
    ) in source_rows:
        source_brand_ids = set()
        for key in fragrance_match_key_variants(
            source_brand_name,
            source_normalized_brand_name,
            regex_preprocess_rules=regex_preprocess_rules,
        ):
            source_brand_ids.update(brand_key_to_ids.get(key, set()))
        if not source_brand_ids:
            continue
        source_name_key = source_normalized_name or normalized_fragrance_key(
            strip_leading_fragrantica_brand_name(source_brand_name, source_name)
        )
        if not source_name_key:
            continue
        for brand_id in source_brand_ids:
            matched_perfume_ids.update(
                perfume_ids_by_brand_name.get((brand_id, source_name_key), set())
            )
    return matched_perfume_ids


def _catalogue_linking_verified_filtered_perfume_ids(
    sequence,
    *,
    min_score: int,
    confidence_filter: str,
    suggestion_filter: str,
    perfume_manager=None,
) -> set[int]:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    perfume_ids = (
        list(sequence.values_list("pk", flat=True))
        if hasattr(sequence, "values_list")
        else [getattr(perfume, "pk", perfume) for perfume in sequence]
    )
    verified_ids: set[int] = set()
    for start in range(0, len(perfume_ids), CATALOGUE_LINKING_BULK_FILTERED_BATCH_SIZE):
        batch_ids = perfume_ids[
            start : start + CATALOGUE_LINKING_BULK_FILTERED_BATCH_SIZE
        ]
        perfumes = list(
            perfume_manager.select_related("brand", "collection").filter(
                pk__in=batch_ids
            )
        )
        rows = build_catalogue_linking_rows(
            perfumes,
            min_score=min_score,
            include_candidates=True,
            include_payload=False,
        )
        rows = filter_catalogue_linking_rows_by_confidence(rows, confidence_filter)
        rows = filter_catalogue_linking_rows_by_suggestion(rows, suggestion_filter)
        verified_ids.update(row["perfume"].id for row in rows)
    return verified_ids


def _catalogue_linking_request_uses_high_confidence_prefilter(request) -> bool:
    confidence_filter = normalize_catalogue_linking_confidence_filter(
        request.GET.get("confidence")
    )
    suggestion_filter = normalize_catalogue_linking_suggestion_filter(
        request.GET.get("suggestions")
    )
    status_filter = normalize_catalogue_linking_status(request.GET.get("status"))
    if suggestion_filter not in {"all", "with"} or status_filter == "linked":
        return False
    is_scoped = bool(
        normalize_fragrantica_review_brand_id(request.GET.get("brand") or "")
        or request.GET.get("q", "").strip()
    )
    if confidence_filter == "100":
        return is_scoped
    if confidence_filter in {"95", "review"}:
        return is_scoped
    return False


def _catalogue_linking_request_uses_review_filter(request) -> bool:
    return (
        normalize_catalogue_linking_confidence_filter(
            request.GET.get("confidence"),
        )
        == "review"
    )


def _catalogue_linking_request_uses_verified_candidate_filter(request) -> bool:
    confidence_filter = normalize_catalogue_linking_confidence_filter(
        request.GET.get("confidence")
    )
    suggestion_filter = normalize_catalogue_linking_suggestion_filter(
        request.GET.get("suggestions")
    )
    status_filter = normalize_catalogue_linking_status(request.GET.get("status"))
    is_scoped = bool(
        normalize_fragrantica_review_brand_id(request.GET.get("brand") or "")
        or request.GET.get("q", "").strip()
    )
    return (
        confidence_filter == "100"
        and suggestion_filter in {"all", "with"}
        and status_filter != "linked"
        and is_scoped
    )


def _build_catalogue_linking_strict_exact_page_rows(
    sequence,
    *,
    page_number: int,
    page_size: int,
    result_size: int | None = None,
    min_score: int,
    max_scan_rows: int | None = None,
) -> list[dict]:
    target_start = max(page_number - 1, 0) * page_size
    target_stop = target_start + (result_size or page_size)
    scan_size = max(page_size * 5, page_size)
    scan_limit = max_scan_rows or page_size * CATALOGUE_LINKING_FILTER_SCAN_PAGES
    matched_seen = 0
    page_rows: list[dict] = []

    for start in range(0, scan_limit, scan_size):
        perfumes = list(
            _catalogue_linking_sequence_slice(
                sequence,
                start,
                start + scan_size,
            )
        )
        if not perfumes:
            break
        visible_rows = build_catalogue_linking_rows(
            perfumes,
            min_score=min_score,
            include_candidates=True,
            include_payload=False,
        )
        filtered_rows = filter_catalogue_linking_rows_by_confidence(
            visible_rows,
            "100",
        )
        for row in filtered_rows:
            if matched_seen >= target_start and matched_seen < target_stop:
                page_rows.append(row)
            matched_seen += 1
            if matched_seen >= target_stop:
                return page_rows
    return page_rows


def _catalogue_linking_filter_batch_rows(
    perfumes,
    *,
    min_score: int,
    confidence_filter: str,
    suggestion_filter: str,
) -> list[dict]:
    perfumes = list(perfumes)
    if not perfumes:
        return []
    if confidence_filter in {"100", "review"} and suggestion_filter in {"all", "with"}:
        exact_perfume_ids = _catalogue_linking_strict_exact_perfume_ids(
            perfumes,
            include_linked_sources=confidence_filter == "review",
        )
        if not exact_perfume_ids:
            return []
        perfumes = [perfume for perfume in perfumes if perfume.id in exact_perfume_ids]
        if not perfumes:
            return []
    rows = build_catalogue_linking_rows(
        perfumes,
        min_score=min_score,
        include_candidates=True,
        include_payload=False,
    )
    rows = filter_catalogue_linking_rows_by_confidence(rows, confidence_filter)
    return filter_catalogue_linking_rows_by_suggestion(rows, suggestion_filter)


def _catalogue_linking_filtered_cache_state(cache_key: str | None) -> dict:
    if not cache_key:
        return {"ids": [], "scan_offset": 0, "exhausted": False}
    cached = cache.get(cache_key)
    if not isinstance(cached, dict):
        return {"ids": [], "scan_offset": 0, "exhausted": False}
    ids = cached.get("ids")
    if not isinstance(ids, list):
        ids = []
    try:
        scan_offset = max(int(cached.get("scan_offset") or 0), 0)
    except (TypeError, ValueError):
        scan_offset = 0
    return {
        "ids": [int(value) for value in ids if str(value).isdigit()],
        "scan_offset": scan_offset,
        "exhausted": bool(cached.get("exhausted")),
    }


def _catalogue_linking_store_filtered_cache_state(
    cache_key: str | None,
    state: dict,
) -> None:
    if not cache_key:
        return
    cache.set(cache_key, state, timeout=CATALOGUE_LINKING_FILTER_CACHE_TTL_SECONDS)


def _build_catalogue_linking_cached_filtered_page_rows(
    sequence,
    *,
    page_number: int,
    page_size: int,
    result_size: int | None,
    min_score: int,
    confidence_filter: str,
    suggestion_filter: str,
    cache_key: str | None,
    max_scan_rows: int | None,
    perfume_manager=None,
) -> CatalogueLinkingFilteredPageRows:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    target_start = max(page_number - 1, 0) * page_size
    target_stop = target_start + (result_size or page_size)
    scan_size = max(page_size * 5, CATALOGUE_LINKING_FILTER_SCAN_MIN_BATCH)
    scan_limit = max_scan_rows or page_size * CATALOGUE_LINKING_FILTER_SCAN_PAGES
    state = _catalogue_linking_filtered_cache_state(cache_key)
    matched_ids = state["ids"]
    scan_offset = state["scan_offset"]
    exhausted = state["exhausted"]
    page_rows: list[dict] = []

    while len(matched_ids) < target_stop and not exhausted and scan_offset < scan_limit:
        perfumes = list(
            _catalogue_linking_sequence_slice(
                sequence,
                scan_offset,
                scan_offset + scan_size,
            )
        )
        if not perfumes:
            exhausted = True
            break
        scan_offset += len(perfumes)
        filtered_rows = _catalogue_linking_filter_batch_rows(
            perfumes,
            min_score=min_score,
            confidence_filter=confidence_filter,
            suggestion_filter=suggestion_filter,
        )
        for row in filtered_rows:
            matched_index = len(matched_ids)
            matched_ids.append(row["perfume"].id)
            if target_start <= matched_index < target_stop:
                page_rows.append(row)

    state = {
        "ids": matched_ids,
        "scan_offset": scan_offset,
        "exhausted": exhausted,
    }
    _catalogue_linking_store_filtered_cache_state(cache_key, state)

    page_ids = matched_ids[target_start:target_stop]
    if not page_ids:
        return CatalogueLinkingFilteredPageRows(
            rows=[],
            known_count=len(matched_ids) if exhausted else None,
            exhausted=exhausted,
        )
    if page_rows and len(page_rows) == len(page_ids):
        return CatalogueLinkingFilteredPageRows(
            rows=page_rows,
            known_count=len(matched_ids) if exhausted else None,
            exhausted=exhausted,
        )
    perfumes_by_id = {
        perfume.id: perfume
        for perfume in perfume_manager.select_related("brand", "collection").filter(
            pk__in=page_ids,
        )
    }
    page_perfumes = [
        perfumes_by_id[perfume_id]
        for perfume_id in page_ids
        if perfume_id in perfumes_by_id
    ]
    rows = _catalogue_linking_filter_batch_rows(
        page_perfumes,
        min_score=min_score,
        confidence_filter=confidence_filter,
        suggestion_filter=suggestion_filter,
    )
    return CatalogueLinkingFilteredPageRows(
        rows=rows,
        known_count=len(matched_ids) if exhausted else None,
        exhausted=exhausted,
    )


def _catalogue_linking_extend_filtered_cache_to_exhaustion(
    sequence,
    *,
    page_size: int,
    min_score: int,
    confidence_filter: str,
    suggestion_filter: str,
    cache_key: str | None,
) -> dict:
    state = _catalogue_linking_filtered_cache_state(cache_key)
    if state["exhausted"]:
        return state

    matched_ids = state["ids"]
    seen_ids = set(matched_ids)
    scan_offset = state["scan_offset"]
    scan_size = max(page_size * 5, CATALOGUE_LINKING_FILTER_SCAN_MIN_BATCH)

    while True:
        perfumes = list(
            _catalogue_linking_sequence_slice(
                sequence,
                scan_offset,
                scan_offset + scan_size,
            )
        )
        if not perfumes:
            state = {
                "ids": matched_ids,
                "scan_offset": scan_offset,
                "exhausted": True,
            }
            _catalogue_linking_store_filtered_cache_state(cache_key, state)
            return state

        scan_offset += len(perfumes)
        filtered_rows = _catalogue_linking_filter_batch_rows(
            perfumes,
            min_score=min_score,
            confidence_filter=confidence_filter,
            suggestion_filter=suggestion_filter,
        )
        for row in filtered_rows:
            perfume_id = row["perfume"].id
            if perfume_id in seen_ids:
                continue
            seen_ids.add(perfume_id)
            matched_ids.append(perfume_id)

        _catalogue_linking_store_filtered_cache_state(
            cache_key,
            {
                "ids": matched_ids,
                "scan_offset": scan_offset,
                "exhausted": False,
            },
        )


def _build_catalogue_linking_filtered_page_rows(
    sequence,
    *,
    page_number: int,
    page_size: int,
    result_size: int | None = None,
    min_score: int,
    confidence_filter: str,
    suggestion_filter: str,
    cache_key: str | None = None,
    max_scan_rows: int | None = None,
) -> CatalogueLinkingFilteredPageRows:
    if cache_key:
        return _build_catalogue_linking_cached_filtered_page_rows(
            sequence,
            page_number=page_number,
            page_size=page_size,
            result_size=result_size,
            min_score=min_score,
            confidence_filter=confidence_filter,
            suggestion_filter=suggestion_filter,
            cache_key=cache_key,
            max_scan_rows=max_scan_rows,
        )
    if confidence_filter == "100" and suggestion_filter in {"all", "with"}:
        rows = _build_catalogue_linking_strict_exact_page_rows(
            sequence,
            page_number=page_number,
            page_size=page_size,
            result_size=result_size,
            min_score=min_score,
            max_scan_rows=max_scan_rows,
        )
        return CatalogueLinkingFilteredPageRows(
            rows=rows, known_count=None, exhausted=False
        )
    if confidence_filter == "review" and suggestion_filter in {"all", "with"}:
        rows = _build_catalogue_linking_review_page_rows(
            sequence,
            page_number=page_number,
            page_size=page_size,
            result_size=result_size,
            min_score=min_score,
            confidence_filter=confidence_filter,
            suggestion_filter=suggestion_filter,
            max_scan_rows=max_scan_rows,
        )
        return CatalogueLinkingFilteredPageRows(
            rows=rows, known_count=None, exhausted=False
        )

    target_start = max(page_number - 1, 0) * page_size
    target_stop = target_start + (result_size or page_size)
    page_rows: list[dict] = []
    scan_size = max(page_size * 5, CATALOGUE_LINKING_FILTER_SCAN_MIN_BATCH)
    scan_limit = max_scan_rows or page_size * CATALOGUE_LINKING_FILTER_SCAN_PAGES
    matched_seen = 0

    for start in range(0, scan_limit, scan_size):
        stop = min(start + scan_size, scan_limit)
        perfumes = list(
            _catalogue_linking_sequence_slice(
                sequence,
                start,
                stop,
            )
        )
        if not perfumes:
            break
        visible_rows = build_catalogue_linking_rows(
            perfumes,
            min_score=min_score,
            include_candidates=True,
            include_payload=False,
        )
        filtered_rows = filter_catalogue_linking_rows_by_confidence(
            visible_rows,
            confidence_filter,
        )
        filtered_rows = filter_catalogue_linking_rows_by_suggestion(
            filtered_rows,
            suggestion_filter,
        )
        for row in filtered_rows:
            if target_start <= matched_seen < target_stop:
                page_rows.append(row)
            matched_seen += 1
            if matched_seen >= target_stop:
                return CatalogueLinkingFilteredPageRows(
                    rows=page_rows,
                    known_count=None,
                    exhausted=False,
                )
    return CatalogueLinkingFilteredPageRows(
        rows=page_rows,
        known_count=None,
        exhausted=len(page_rows) < (result_size or page_size),
    )


def _build_catalogue_linking_review_page_rows(
    sequence,
    *,
    page_number: int,
    page_size: int,
    result_size: int | None,
    min_score: int,
    confidence_filter: str,
    suggestion_filter: str,
    max_scan_rows: int | None = None,
) -> list[dict]:
    target_start = max(page_number - 1, 0) * page_size
    target_stop = target_start + (result_size or page_size)
    page_rows: list[dict] = []
    scan_size = max(page_size * 5, CATALOGUE_LINKING_FILTER_SCAN_MIN_BATCH)
    scan_limit = max_scan_rows or page_size * CATALOGUE_LINKING_FILTER_SCAN_PAGES
    matched_seen = 0

    for start in range(0, scan_limit, scan_size):
        stop = min(start + scan_size, scan_limit)
        perfumes = list(_catalogue_linking_sequence_slice(sequence, start, stop))
        if not perfumes:
            break
        exact_perfume_ids = _catalogue_linking_strict_exact_perfume_ids(
            perfumes,
            include_linked_sources=True,
        )
        if not exact_perfume_ids:
            continue
        candidate_perfumes = [
            perfume for perfume in perfumes if perfume.id in exact_perfume_ids
        ]
        visible_rows = build_catalogue_linking_rows(
            candidate_perfumes,
            min_score=min_score,
            include_candidates=True,
            include_payload=False,
        )
        filtered_rows = filter_catalogue_linking_rows_by_confidence(
            visible_rows,
            confidence_filter,
        )
        filtered_rows = filter_catalogue_linking_rows_by_suggestion(
            filtered_rows,
            suggestion_filter,
        )
        for row in filtered_rows:
            if target_start <= matched_seen < target_stop:
                page_rows.append(row)
            matched_seen += 1
            if matched_seen >= target_stop:
                return page_rows
    return page_rows


def _catalogue_linking_filtered_countless_page(
    *,
    rows: list[dict],
    page_number: int,
    page_size: int,
    object_list,
    total_count: int | None = None,
) -> tuple[list[dict], CountlessPaginator, CountlessPage]:
    has_next = len(rows) > page_size
    page_rows = rows[:page_size]
    paginator = CountlessPaginator(
        per_page=page_size,
        current_page=page_number,
        has_next=has_next,
        object_list=object_list,
        total_count=total_count,
    )
    if not page_rows and page_number > 1:
        paginator.num_pages = 1
    page_obj = CountlessPage(
        object_list=page_rows,
        number=page_number,
        paginator=paginator,
        _has_next=has_next,
    )
    return page_rows, paginator, page_obj


def build_catalogue_linking_count_payload(
    request,
    *,
    page_size: int,
    perfume_manager=None,
) -> dict:
    min_score = normalize_catalogue_linking_min_score(request.GET.get("min_score"))
    confidence_filter = normalize_catalogue_linking_confidence_filter(
        request.GET.get("confidence")
    )
    if confidence_filter != "all":
        min_score = normalize_catalogue_linking_min_score(confidence_filter)
    if confidence_filter == "review":
        min_score = max(min_score, 95)
    suggestion_filter = normalize_catalogue_linking_suggestion_filter(
        request.GET.get("suggestions")
    )
    row_filter_active = suggestion_filter != "all" or confidence_filter != "all"
    sequence = build_catalogue_linking_perfume_queryset(
        request,
        perfume_manager=perfume_manager,
    )
    if row_filter_active:
        state = _catalogue_linking_extend_filtered_cache_to_exhaustion(
            sequence,
            page_size=page_size,
            min_score=min_score,
            confidence_filter=confidence_filter,
            suggestion_filter=suggestion_filter,
            cache_key=_catalogue_linking_filter_cache_key(
                request,
                page_size=page_size,
                min_score=min_score,
            ),
        )
        total_count = len(state["ids"])
    else:
        total_count = _catalogue_linking_sequence_count(sequence)

    page_number = parse_page_number(request.GET.get("page"))
    num_pages = max((total_count + page_size - 1) // page_size, 1)
    page_number = min(page_number, num_pages)
    paginator = CountlessPaginator(
        per_page=page_size,
        current_page=page_number,
        has_next=page_number < num_pages,
        object_list=sequence,
        total_count=total_count,
    )
    page_obj = CountlessPage(
        object_list=[],
        number=page_number,
        paginator=paginator,
        _has_next=page_number < num_pages,
    )
    return {
        "count": total_count,
        "pages": num_pages,
        "page": page_number,
        "row_count_display": f"{total_count} rows",
        "visible_count_display": str(total_count),
        "page_obj": page_obj,
        "paginator": paginator,
    }


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
    if confidence_filter == "review":
        min_score = max(min_score, 95)
    suggestion_filter = normalize_catalogue_linking_suggestion_filter(
        request.GET.get("suggestions")
    )
    perfumes = list(list_context.get("perfumes", []))
    row_filter_active = suggestion_filter != "all" or confidence_filter != "all"
    paginator = list_context.get("paginator")
    page_obj = list_context.get("page_obj")
    visible_count = getattr(paginator, "count", None) if paginator else len(perfumes)
    if (
        row_filter_active
        and paginator
        and page_obj
        and _catalogue_linking_request_uses_verified_candidate_filter(request)
    ):
        verified_ids = _catalogue_linking_verified_filtered_perfume_ids(
            paginator.object_list,
            min_score=min_score,
            confidence_filter=confidence_filter,
            suggestion_filter=suggestion_filter,
        )
        verified_sequence = paginator.object_list.filter(pk__in=verified_ids)
        paginator = Paginator(verified_sequence, page_obj.paginator.per_page)
        page_obj = paginator.get_page(request.GET.get("page"))
        visible_count = paginator.count
        visible_rows = build_catalogue_linking_rows(
            page_obj.object_list,
            min_score=min_score,
            include_candidates=True,
            include_payload=False,
        )
        rows = filter_catalogue_linking_rows_by_confidence(
            visible_rows,
            confidence_filter,
        )
        rows = filter_catalogue_linking_rows_by_suggestion(
            rows,
            suggestion_filter,
        )
    elif row_filter_active and paginator and page_obj:
        visible_rows = []
        display_page_size = page_obj.paginator.per_page
        filtered_page_result = _build_catalogue_linking_filtered_page_rows(
            paginator.object_list,
            page_number=page_obj.number,
            page_size=display_page_size,
            result_size=display_page_size + 1,
            min_score=min_score,
            confidence_filter=confidence_filter,
            suggestion_filter=suggestion_filter,
            cache_key=_catalogue_linking_filter_cache_key(
                request,
                page_size=display_page_size,
                min_score=min_score,
            ),
        )
        rows, paginator, page_obj = _catalogue_linking_filtered_countless_page(
            rows=filtered_page_result.rows,
            page_number=page_obj.number,
            page_size=display_page_size,
            object_list=paginator.object_list,
            total_count=filtered_page_result.known_count,
        )
        visible_count = filtered_page_result.known_count
    else:
        visible_rows = build_catalogue_linking_rows(
            perfumes,
            min_score=min_score,
            include_candidates=False,
        )
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
    if selected_row and not selected_row.get("payload_json"):
        selected_row["payload_json"] = catalogue_linking_row_payload_json(selected_row)
    suggestion_visible_count_display = _countless_visible_count_display(
        page_obj,
        visible_count,
    )
    catalogue_linking_row_count_display = _countless_page_result_display(
        page_obj,
        noun="rows",
    )
    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)
    return {
        "active_tab": "linking",
        "brands": brand_manager.only("id", "name").order_by("name"),
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
        "suggestion_visible_count_display": suggestion_visible_count_display,
        "suggestion_filtered_count": len(rows),
        "row_filter_active": row_filter_active,
        "catalogue_linking_row_count_display": catalogue_linking_row_count_display,
        "selected_row": selected_row,
        "ready_bulk_count": sum(1 for row in rows if row["ready_for_bulk"]),
        "query_string": query_without_page.urlencode(),
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": paginator.num_pages > 1 if paginator else False,
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
                "selected": serialize_catalogue_linking_selected_perfume(perfume),
                "linked_sources": [
                    serialize_catalogue_linking_source(source)
                    for source in linked_sources
                ],
                "candidates": [],
            },
            200,
        )
    conflict_scope_perfumes = list(
        _catalogue_linking_candidate_conflict_scope(perfume, perfume_manager)
    )
    candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
        conflict_scope_perfumes,
        min_score=min_score,
        limit=12,
    )
    candidates = candidate_map.get(perfume.id, [])
    latest_advice = (
        latest_fragrantica_rerank_recommendation(
            perfume=perfume,
            candidates=candidates,
        )
        if candidates
        else None
    )
    return (
        {
            "selected": serialize_catalogue_linking_selected_perfume(perfume),
            "candidates": [
                serialize_catalogue_linking_candidate(candidate)
                for candidate in candidates
            ],
            "linked_sources": [],
            "ai_advice": (
                serialize_catalogue_linking_ai_advice(latest_advice)
                if latest_advice
                else None
            ),
        },
        200,
    )


def build_catalogue_linking_fragrantica_search_payload(
    request,
    *,
    perfume_manager=None,
    source_manager=None,
) -> tuple[dict, int]:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    source_manager = source_manager or FragranticaProduct.objects
    perfume_id = request.GET.get("perfume")
    if not perfume_id:
        return {"error": "Choose an Our Products row first."}, 400
    perfume = first_from_queryset(
        perfume_manager.select_related("brand", "collection").filter(pk=perfume_id)
    )
    if not perfume:
        return {"error": "Our Products row was not found."}, 404
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return (
            {
                "selected": serialize_catalogue_linking_selected_perfume(perfume),
                "results": [],
                "message": "Type at least 2 characters to search Fragrantica.",
            },
            200,
        )
    queryset = (
        source_manager.select_related("matched_perfume", "matched_perfume__brand")
        .exclude(match_status=FragranticaProduct.STATUS_IGNORED)
        .filter(fragrantica_product_search_filter(query))
        .order_by("brand_name", "collection_name", "name", "release_year", "id")
    )
    perfume_brand_key = normalized_fragrance_key(perfume.brand.name)
    sources = list(queryset[:80])
    sources.sort(
        key=lambda source: (
            normalized_fragrance_key(source.brand_name) != perfume_brand_key,
            source.match_status == FragranticaProduct.STATUS_LINKED
            and source.matched_perfume_id != perfume.id,
            source.match_status == FragranticaProduct.STATUS_LINKED,
            source.brand_name,
            fragrantica_source_catalogue_name(source),
            source.release_year or 0,
            source.id,
        )
    )
    results = [
        serialize_catalogue_linking_manual_source(source, perfume)
        for source in sources[:20]
    ]
    return (
        {
            "selected": serialize_catalogue_linking_selected_perfume(perfume),
            "results": results,
            "message": "" if results else "No Fragrantica rows matched that search.",
        },
        200,
    )


def build_catalogue_linking_ai_advice_payload(
    request,
    *,
    perfume_manager=None,
) -> tuple[dict, int]:
    if not use_openai():
        return (
            {
                "error": (
                    "OpenAI is not enabled. Set ASSISTANT_USE_OPENAI=true and "
                    "OPENAI_API_KEY to generate AI advice."
                )
            },
            409,
        )

    perfume_manager = perfume_manager or CatalogPerfume.objects
    perfume_id = request.POST.get("perfume") or request.GET.get("perfume")
    if not perfume_id:
        return {"error": "Choose an Our Products row first."}, 400
    perfume = first_from_queryset(
        perfume_manager.select_related("brand", "collection").filter(pk=perfume_id)
    )
    if not perfume:
        return {"error": "Our Products row was not found."}, 404

    linked_sources = build_linked_fragrantica_sources_by_perfume_ids(
        [perfume.id],
    ).get(perfume.id, [])
    if linked_sources:
        return {"error": "This Our Products row is already linked."}, 409

    min_score = normalize_catalogue_linking_min_score(
        request.POST.get("min_score") or request.GET.get("min_score")
    )
    candidates = _catalogue_linking_candidates_for_ai_advice(
        perfume,
        perfume_manager,
        min_score=min_score,
    )
    if not candidates:
        return {"error": "No Fragrantica candidates are available for AI review."}, 400

    recommendation = create_fragrantica_rerank_recommendation(
        perfume=perfume,
        candidates=candidates,
        call_model=True,
    )
    return (
        {
            "selected": serialize_catalogue_linking_selected_perfume(perfume),
            "candidates": [
                serialize_catalogue_linking_candidate(candidate)
                for candidate in candidates
            ],
            "linked_sources": [],
            "ai_advice": serialize_catalogue_linking_ai_advice(recommendation),
        },
        200,
    )


def _catalogue_linking_candidates_for_ai_advice(
    perfume,
    perfume_manager,
    *,
    min_score: int,
) -> list[FragranticaMatchCandidate]:
    conflict_scope_perfumes = list(
        _catalogue_linking_candidate_conflict_scope(perfume, perfume_manager)
    )
    candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
        conflict_scope_perfumes,
        min_score=min_score,
        limit=12,
    )
    candidates = candidate_map.get(perfume.id, [])
    if candidates:
        return candidates
    fallback_map = build_catalogue_fragrantica_candidates_for_perfumes(
        [perfume],
        min_score=min_score,
        limit=12,
    )
    return fallback_map.get(perfume.id, [])


def build_catalogue_linking_ai_advice_review_payload(
    recommendation_id,
    post_data,
    *,
    user=None,
    recommendation_manager=None,
) -> tuple[dict, int]:
    recommendation_manager = recommendation_manager or AIRecommendation.objects
    recommendation = get_object_or_404(recommendation_manager, pk=recommendation_id)
    action = post_data.get("action", "")
    if action == "accept":
        recommendation.status = AIRecommendation.STATUS_ACCEPTED
    elif action == "reject":
        recommendation.status = AIRecommendation.STATUS_REJECTED
    else:
        return {"error": "Choose accept or reject for the AI recommendation."}, 400

    recommendation.reviewed_by = (
        user if getattr(user, "is_authenticated", False) else None
    )
    recommendation.reviewed_at = timezone.now()
    recommendation.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
    )
    sync_learning_proposal_for_recommendation(
        recommendation,
        user=user,
        action=action,
    )
    return {"ai_advice": serialize_catalogue_linking_ai_advice(recommendation)}, 200


def _catalogue_linking_filter_request_from_post(post_data):
    query = QueryDict("", mutable=True)
    for key in ("brand", "q", "status", "suggestions", "confidence"):
        query[key] = (post_data.get(key) or "").strip()
    return SimpleNamespace(GET=query)


def catalogue_linking_filtered_bulk_command_options(post_data) -> dict[str, str]:
    return {
        "brand": (post_data.get("brand") or "").strip(),
        "q": (post_data.get("q") or "").strip(),
        "status": normalize_catalogue_linking_status(post_data.get("status")),
        "suggestions": normalize_catalogue_linking_suggestion_filter(
            post_data.get("suggestions")
        ),
        "confidence": normalize_catalogue_linking_confidence_filter(
            post_data.get("confidence")
        ),
    }


def _catalogue_linking_safe_bulk_filtered_settings(post_data) -> tuple[str, str, int]:
    confidence_filter = normalize_catalogue_linking_confidence_filter(
        post_data.get("confidence")
    )
    suggestion_filter = normalize_catalogue_linking_suggestion_filter(
        post_data.get("suggestions")
    )
    status_filter = normalize_catalogue_linking_status(post_data.get("status"))
    if status_filter == "linked":
        raise ValueError("All-page bulk linking is only available for unlinked rows.")
    if suggestion_filter == "without":
        raise ValueError("Use a suggestion filter that includes suggested rows.")
    if confidence_filter not in {"95", "100"}:
        raise ValueError(
            "Choose 95+ or 100 only before linking suggestions across all pages."
        )
    return (
        confidence_filter,
        suggestion_filter,
        normalize_catalogue_linking_min_score(confidence_filter),
    )


def _catalogue_linking_filtered_ready_pairs(
    post_data,
    *,
    perfume_manager=None,
) -> tuple[list[tuple[int, int, bool]], int]:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    confidence_filter, suggestion_filter, min_score = (
        _catalogue_linking_safe_bulk_filtered_settings(post_data)
    )
    filter_request = _catalogue_linking_filter_request_from_post(post_data)
    queryset = build_catalogue_linking_perfume_queryset(
        filter_request,
        perfume_manager=perfume_manager,
    )
    perfume_ids = list(queryset.values_list("pk", flat=True))
    if not perfume_ids:
        return [], 0

    raw_pairs: list[tuple[int, int, bool]] = []
    skipped_count = 0
    for start in range(
        0,
        len(perfume_ids),
        CATALOGUE_LINKING_BULK_FILTERED_BATCH_SIZE,
    ):
        batch_ids = perfume_ids[
            start : start + CATALOGUE_LINKING_BULK_FILTERED_BATCH_SIZE
        ]
        perfumes = list(
            perfume_manager.select_related("brand", "collection").filter(
                pk__in=batch_ids
            )
        )
        rows = build_catalogue_linking_rows(
            perfumes,
            min_score=min_score,
            include_candidates=True,
            include_payload=False,
        )
        rows = filter_catalogue_linking_rows_by_confidence(rows, confidence_filter)
        rows = filter_catalogue_linking_rows_by_suggestion(rows, suggestion_filter)
        for row in rows:
            candidate = row["top_candidate"]
            if not row["ready_for_bulk"] or not candidate:
                skipped_count += 1
                continue
            raw_pairs.append(
                (
                    candidate.source.id,
                    row["perfume"].id,
                    bool(candidate.creates_alias),
                )
            )

    source_counts: dict[int, int] = defaultdict(int)
    for source_id, _perfume_id, _create_alias in raw_pairs:
        source_counts[source_id] += 1
    safe_pairs = [pair for pair in raw_pairs if source_counts[pair[0]] == 1]
    skipped_count += len(raw_pairs) - len(safe_pairs)
    return safe_pairs, skipped_count


def _run_catalogue_linking_selected_pairs(
    selected_pairs,
    *,
    skipped_count: int,
    redirect_url: str,
    action: str,
    host: str = "",
    source_manager,
    perfume_manager,
) -> CatalogueLinkingBulkResult:
    linked_count = 0
    for raw_pair in selected_pairs:
        if isinstance(raw_pair, tuple):
            source_id, perfume_id, create_alias = raw_pair
        else:
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
            (
                f"Linked {linked_count} Fragrantica match(es)"
                f"{' across all filtered pages' if action == 'bulk_link_filtered' else ''}."
                f"{skipped_note}"
            ),
            redirect_url,
        )
    return CatalogueLinkingBulkResult(
        "error",
        "No selected suggestions could be linked.",
        redirect_url,
    )


def run_catalogue_linking_filtered_bulk_action(
    post_data,
    *,
    redirect_url: str = "",
    source_manager=None,
    perfume_manager=None,
) -> CatalogueLinkingBulkResult:
    redirect_url = redirect_url or reverse("prices:catalogue_linking_workbench")
    source_manager = source_manager or FragranticaProduct.objects
    perfume_manager = perfume_manager or CatalogPerfume.objects
    try:
        selected_pairs, skipped_count = _catalogue_linking_filtered_ready_pairs(
            post_data,
            perfume_manager=perfume_manager,
        )
    except ValueError as exc:
        return CatalogueLinkingBulkResult("error", str(exc), redirect_url)
    if not selected_pairs:
        return CatalogueLinkingBulkResult(
            "error",
            "No safe ready suggestions matched the current filters.",
            redirect_url,
        )
    return _run_catalogue_linking_selected_pairs(
        selected_pairs,
        skipped_count=skipped_count,
        redirect_url=redirect_url,
        action="bulk_link_filtered",
        host="",
        source_manager=source_manager,
        perfume_manager=perfume_manager,
    )


def _catalogue_linking_candidate_conflict_scope(perfume, perfume_manager):
    queryset = perfume_manager.select_related("brand", "collection").filter(
        brand=perfume.brand,
        name__iexact=perfume.name,
    )
    if not queryset.filter(pk=perfume.pk).exists():
        queryset = perfume_manager.select_related("brand", "collection").filter(
            Q(pk=perfume.pk) | Q(brand=perfume.brand, name__iexact=perfume.name)
        )
    return queryset


def _raw_delete_queryset(queryset) -> int:
    return queryset._raw_delete(queryset.db)


def _model_update(
    app_label: str, model_name: str, filters: dict, updates: dict
) -> None:
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return
    model.objects.filter(**filters).update(**updates)


def _fast_unlink_catalogue_variants(variant_ids: list[int]) -> None:
    if not variant_ids:
        return
    models.SupplierProduct.objects.filter(catalog_variant_id__in=variant_ids).update(
        catalog_variant=None,
    )
    _model_update(
        "assistant_linking",
        "MatchGroup",
        {"candidate_variant_id__in": variant_ids},
        {"candidate_variant": None},
    )
    _model_update(
        "assistant_linking",
        "ManualLinkDecision",
        {"variant_id__in": variant_ids},
        {"variant": None},
    )
    _model_update(
        "assistant_linking",
        "LinkSuggestion",
        {"suggested_variant_id__in": variant_ids},
        {"suggested_variant": None},
    )


def _fast_delete_catalogue_variants(variant_ids: list[int]) -> int:
    if not variant_ids:
        return 0
    variant_ids = [int(variant_id) for variant_id in variant_ids]
    variant_queryset = CatalogPerfumeVariant.objects.filter(pk__in=variant_ids)
    deleted_count = variant_queryset.count()
    if not deleted_count:
        return 0
    _fast_unlink_catalogue_variants(variant_ids)
    _raw_delete_queryset(variant_queryset)
    return deleted_count


def _fast_unlink_catalogue_perfumes(perfume_ids: list[int]) -> list[int]:
    variant_ids = list(
        CatalogPerfumeVariant.objects.filter(perfume_id__in=perfume_ids).values_list(
            "pk",
            flat=True,
        )
    )
    _fast_unlink_catalogue_variants(variant_ids)
    models.SupplierProduct.objects.filter(catalog_perfume_id__in=perfume_ids).update(
        catalog_perfume=None,
    )
    FragranticaProduct.objects.filter(matched_perfume_id__in=perfume_ids).update(
        matched_perfume=None,
        match_status=FragranticaProduct.STATUS_UNLINKED,
    )
    ProductAlias.objects.filter(perfume_id__in=perfume_ids).update(perfume=None)
    _model_update(
        "assistant_core",
        "KnowledgeNote",
        {"perfume_id__in": perfume_ids},
        {"perfume": None},
    )
    _model_update(
        "assistant_core",
        "ResearchJob",
        {"perfume_id__in": perfume_ids},
        {"perfume": None},
    )
    _model_update(
        "assistant_core",
        "DetectedChange",
        {"perfume_id__in": perfume_ids},
        {"perfume": None},
    )
    _model_update(
        "assistant_linking",
        "MatchGroup",
        {"candidate_perfume_id__in": perfume_ids},
        {"candidate_perfume": None},
    )
    _model_update(
        "assistant_linking",
        "ManualLinkDecision",
        {"perfume_id__in": perfume_ids},
        {"perfume": None},
    )
    _model_update(
        "assistant_linking",
        "LinkSuggestion",
        {"suggested_perfume_id__in": perfume_ids},
        {"suggested_perfume": None},
    )
    AIRecommendation.objects.filter(perfume_id__in=perfume_ids).update(perfume=None)
    return variant_ids


def _fast_delete_catalogue_perfumes(perfume_ids: list[int]) -> int:
    if not perfume_ids:
        return 0
    perfume_ids = [int(perfume_id) for perfume_id in perfume_ids]
    perfume_queryset = CatalogPerfume.objects.filter(pk__in=perfume_ids)
    deleted_count = perfume_queryset.count()
    if not deleted_count:
        return 0
    variant_ids = _fast_unlink_catalogue_perfumes(perfume_ids)
    FragranticaProductLink.objects.filter(perfume_id__in=perfume_ids).delete()
    CatalogAIDraft.objects.filter(perfume_id__in=perfume_ids).delete()
    CatalogPerfumeAccord.objects.filter(perfume_id__in=perfume_ids).delete()
    CatalogPerfumeNote.objects.filter(perfume_id__in=perfume_ids).delete()
    CatalogFactClaim.objects.filter(perfume_id__in=perfume_ids).delete()
    CatalogSource.objects.filter(perfume_id__in=perfume_ids).delete()
    if variant_ids:
        _raw_delete_queryset(CatalogPerfumeVariant.objects.filter(pk__in=variant_ids))
    _raw_delete_queryset(perfume_queryset)
    return deleted_count


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
    action_values = (
        post_data.getlist("action")
        if hasattr(post_data, "getlist")
        else [post_data.get("action")]
    )
    action = next((value for value in reversed(action_values) if value), "")
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
        with transaction.atomic():
            if (
                source_manager.model is FragranticaProduct
                and perfume_manager.model is CatalogPerfume
            ):
                _fast_delete_catalogue_perfumes(
                    [int(perfume_id) for perfume_id in clean_ids],
                )
            else:
                source_manager.filter(matched_perfume_id__in=clean_ids).update(
                    matched_perfume=None,
                    match_status=FragranticaProduct.STATUS_UNLINKED,
                )
                FragranticaProductLink.objects.filter(perfume_id__in=clean_ids).delete()
                queryset.delete()
        return CatalogueLinkingBulkResult(
            "success",
            f"Deleted {deleted_count} Our Products perfume row(s).",
            redirect_url,
        )

    if action == "bulk_link_filtered":
        try:
            _catalogue_linking_safe_bulk_filtered_settings(post_data)
        except ValueError as exc:
            return CatalogueLinkingBulkResult("error", str(exc), redirect_url)
        if (
            not settings.PERFUMEX_RQ_SYNC
            and source_manager.model is FragranticaProduct
            and perfume_manager.model is CatalogPerfume
        ):
            try:
                job = enqueue_management_command(
                    "bulk_link_catalogue_filtered",
                    queue_name=settings.RQ_DEFAULT_QUEUE,
                    description="Bulk link catalogue Fragrantica matches",
                    **catalogue_linking_filtered_bulk_command_options(post_data),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to queue catalogue filtered bulk link: %s",
                    exc,
                )
                return CatalogueLinkingBulkResult(
                    "error",
                    f"Could not queue all-filtered bulk linking: {exc}",
                    redirect_url,
                )
            return CatalogueLinkingBulkResult(
                "success",
                (
                    "Queued all-filtered Fragrantica bulk linking "
                    f"(job {job.job_id or job.status}). Refresh this page in a minute."
                ),
                redirect_url,
            )
        return run_catalogue_linking_filtered_bulk_action(
            post_data,
            redirect_url=redirect_url,
            source_manager=source_manager,
            perfume_manager=perfume_manager,
        )
    elif action == "bulk_link":
        selected_pairs = post_data.getlist("link_pair")
        skipped_count = 0
        if not selected_pairs:
            return CatalogueLinkingBulkResult(
                "error",
                "Select at least one suggested Fragrantica match.",
                redirect_url,
            )
    else:
        return CatalogueLinkingBulkResult(
            "error", "Unknown linking action.", redirect_url
        )

    linked_count = 0
    for raw_pair in selected_pairs:
        if isinstance(raw_pair, tuple):
            source_id, perfume_id, create_alias = raw_pair
        else:
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
            (
                f"Linked {linked_count} Fragrantica match(es)"
                f"{' across all filtered pages' if action == 'bulk_link_filtered' else ''}."
                f"{skipped_note}"
            ),
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
                "display_name": fragrantica_source_catalogue_display_name(row),
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
        source_queryset = fragrantica_manager.filter(
            brand_name__iexact=source.brand_name
        )
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


def apply_fragrantica_identity_to_perfume(
    source,
    perfume,
    *,
    update_name: bool = True,
) -> list[str]:
    changed_fields = []
    if update_name:
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
        audience_group_from_text(
            source.audience, fragrantica_source_catalogue_name(source)
        )
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
    canonical_source_name = display_name_without_concentration(
        reviewed_fragrantica_perfume_name(source, perfume)
    )
    canonical_text = normalize_catalogue_perfume_name(
        canonical_source_name or fragrantica_source_catalogue_name(source)
    ).strip()
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


def record_fragrantica_review_link(
    source,
    perfume,
    *,
    link_type: str,
    note: str = "",
) -> None:
    FragranticaProductLink.objects.update_or_create(
        source=source,
        perfume=perfume,
        defaults={
            "link_type": link_type,
            "note": note,
        },
    )


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
    is_manual_extra_link = (
        source.matched_perfume_id and source.matched_perfume_id != perfume.id
    )
    old_name = perfume.name
    create_alias = post_data.get("create_alias") == "1"
    apply_identity_group = post_data.get("apply_identity_group") == "1"
    update_name = post_data.get("update_name", "1") != "0"
    perfume_group = (
        build_fragrantica_identity_perfume_group(perfume, source=source)
        if apply_identity_group
        else [perfume]
    )
    if is_manual_extra_link:
        record_fragrantica_review_link(
            source,
            perfume,
            link_type=FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
            note="Approved rare second Our Products link to one Fragrantica row.",
        )
    else:
        source.matched_perfume = perfume
        source.match_status = FragranticaProduct.STATUS_LINKED
        source.save(update_fields=["matched_perfume", "match_status", "updated_at"])
        record_fragrantica_review_link(
            source,
            perfume,
            link_type=FragranticaProductLink.LINK_TYPE_PRIMARY,
        )
    changed_field_names = set()
    source_count = 0
    for group_perfume in perfume_group:
        changed_fields = apply_fragrantica_identity_to_perfume(
            source,
            group_perfume,
            update_name=update_name,
        )
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
    name_note = " Local product name was preserved." if not update_name else ""
    link_message = (
        "Added reviewed second Fragrantica link"
        if is_manual_extra_link
        else "Linked Fragrantica row"
    )
    return FragranticaCatalogueLinkResult(
        "success",
        f"{link_message} to {perfume.brand.name} / {perfume.name}."
        f"{changed_note}{group_note}{source_note}{alias_note}{name_note}{preserved_note}",
        redirect_url,
    )


def run_fragrantica_catalogue_unlink_action(
    source_id,
    post_data,
    *,
    host: str = "",
    source_getter=None,
    perfume_getter=None,
    link_manager=None,
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

    link_manager = link_manager or FragranticaProductLink.objects
    redirect_url = post_data.get("next", reverse("prices:catalogue_linking_workbench"))
    if not url_has_allowed_host_and_scheme(
        redirect_url,
        allowed_hosts={host} if host else None,
    ):
        redirect_url = reverse("prices:catalogue_linking_workbench")

    perfume_id = post_data.get("perfume_id")
    if not perfume_id:
        return FragranticaCatalogueLinkResult(
            "error",
            "Choose an Our Products catalogue row before unlinking.",
            redirect_url,
        )

    source = source_getter(source_id)
    perfume = perfume_getter(perfume_id)
    link_queryset = link_manager.filter(source=source, perfume=perfume)
    is_primary_link = source.matched_perfume_id == perfume.id
    if not is_primary_link and not link_queryset.exists():
        return FragranticaCatalogueLinkResult(
            "error",
            "This Fragrantica row is not linked to that Our Products row.",
            redirect_url,
        )

    deleted_count = link_queryset.count()
    link_queryset.delete()
    if is_primary_link:
        source.matched_perfume = None
        source.match_status = FragranticaProduct.STATUS_UNLINKED
        source.save(update_fields=["matched_perfume", "match_status", "updated_at"])
        relationship_label = "primary Fragrantica link"
    else:
        relationship_label = "reviewed extra Fragrantica link"

    removed_note = " Removed reviewed link record." if deleted_count else ""
    return FragranticaCatalogueLinkResult(
        "success",
        f"Unlinked {relationship_label} from {perfume.brand.name} / {perfume.name}.{removed_note}",
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
        with transaction.atomic():
            if variant_manager.model is CatalogPerfumeVariant:
                _fast_delete_catalogue_variants(
                    [int(variant_id) for variant_id in clean_ids],
                )
            else:
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


def _catalogue_variant_identity_exists(variant, perfume, update_data) -> bool:
    return (
        CatalogPerfumeVariant.objects.filter(
            perfume=perfume,
            size_ml=update_data.size_ml,
            packaging=update_data.packaging,
            variant_type=update_data.variant_type,
            is_tester=update_data.is_tester,
        )
        .exclude(pk=variant.pk)
        .exists()
    )


def _matching_perfume_for_variant_update(
    variant,
    update_data,
    brand,
    *,
    perfume_manager=None,
):
    if not getattr(variant, "perfume_id", None):
        return None
    perfume_manager = perfume_manager or CatalogPerfume.objects
    target_name_key = normalized_fragrance_key(update_data.perfume_name)
    target_concentration_key = normalized_fragrance_key(update_data.concentration)
    requested_collection_key = normalized_fragrance_key(update_data.collection_name)
    candidates = (
        perfume_manager.filter(brand=brand)
        .exclude(pk=variant.perfume_id)
        .select_related("brand", "collection")
        .order_by("id")
    )
    for candidate in candidates:
        if normalized_fragrance_key(candidate.name) != target_name_key:
            continue
        if (
            normalized_fragrance_key(candidate.concentration)
            != target_concentration_key
        ):
            continue
        candidate_collection_key = normalized_fragrance_key(candidate.collection_name)
        if (
            requested_collection_key
            and candidate_collection_key
            and requested_collection_key != candidate_collection_key
        ):
            continue
        return candidate
    return None


def _delete_empty_unlinked_catalogue_perfume(perfume) -> None:
    if not perfume or not getattr(perfume, "pk", None):
        return
    if CatalogPerfumeVariant.objects.filter(perfume=perfume).exists():
        return
    if FragranticaProduct.objects.filter(matched_perfume=perfume).exists():
        return
    if FragranticaProductLink.objects.filter(perfume=perfume).exists():
        return
    if models.SupplierProduct.objects.filter(catalog_perfume=perfume).exists():
        return
    _fast_delete_catalogue_perfumes([perfume.id])


def apply_catalog_variant_inline_update(
    variant,
    post_data,
    *,
    brand_manager=None,
    perfume_manager=None,
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
    target_perfume = _matching_perfume_for_variant_update(
        variant,
        update_data,
        brand,
        perfume_manager=perfume_manager,
    )
    if target_perfume and _catalogue_variant_identity_exists(
        variant,
        target_perfume,
        update_data,
    ):
        return CatalogVariantInlineUpdateResult(
            "error",
            "A catalogue variant with this product identity already exists.",
        )
    if target_perfume:
        variant.perfume = target_perfume
    else:
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
    update_fields = [
        "size_ml",
        "size_label",
        "is_tester",
        "packaging",
        "variant_type",
        "updated_at",
    ]
    if target_perfume:
        update_fields.insert(0, "perfume")
    variant.save(
        update_fields=update_fields,
    )
    if target_perfume:
        models.SupplierProduct.objects.filter(catalog_variant=variant).update(
            catalog_perfume=target_perfume,
        )
        _delete_empty_unlinked_catalogue_perfume(perfume)
        return CatalogVariantInlineUpdateResult(
            "success",
            "Product row updated and joined existing catalogue identity.",
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
