from __future__ import annotations

import hashlib
from typing import Any

from django.db.models import Count, Q
from django.utils import timezone

from assistant_linking.models import NormalizationStatsSnapshot, ParsedSupplierProduct
from assistant_linking.services.garbage import GARBAGE_MODIFIER
from assistant_linking.services.normalizer import PARSER_VERSION
from prices.models import SupplierProduct
from prices.services.product_visibility import apply_hidden_product_keywords


SUPPLIER_PRODUCT_HIDDEN_FIELDS = ("name", "brand", "supplier_sku")
PARSED_PRODUCT_HIDDEN_FIELDS = (
    "supplier_product__name",
    "supplier_product__brand",
    "supplier_product__supplier_sku",
)
COUNT_KEYS = (
    "parsed_count",
    "unparsed_count",
    "low_confidence_count",
    "missing_brand_count",
    "missing_name_count",
    "missing_concentration_count",
    "missing_size_count",
    "modifier_count",
    "garbage_count",
    "tester_sample_count",
    "set_count",
)


def complete_parse_query() -> Q:
    return (
        Q(normalized_brand__isnull=False)
        & ~Q(product_name_text="")
        & ~Q(concentration="")
        & Q(size_ml__isnull=False)
        & Q(is_set=False)
    )


def snapshot_scope_key(hidden_keywords: list[str]) -> str:
    digest = hidden_keywords_hash(hidden_keywords)[:16]
    return f"hidden:{digest}" if hidden_keywords else "global"


def hidden_keywords_hash(hidden_keywords: list[str]) -> str:
    normalized = "\n".join(sorted(keyword.strip().lower() for keyword in hidden_keywords if keyword.strip()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def empty_stats() -> dict[str, Any]:
    return {
        **{key: "..." for key in COUNT_KEYS},
        "recent_ids": [],
        "stats_available": False,
        "stats_stale": True,
        "stats_generated_at": None,
    }


def snapshot_to_stats(snapshot: NormalizationStatsSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return empty_stats()
    return {
        **{key: getattr(snapshot, key) for key in COUNT_KEYS},
        "recent_ids": snapshot.recent_parse_ids or [],
        "stats_available": True,
        "stats_stale": snapshot.is_stale,
        "stats_generated_at": snapshot.generated_at,
    }


def get_stats_snapshot(*, hidden_keywords: list[str]) -> NormalizationStatsSnapshot | None:
    return NormalizationStatsSnapshot.objects.filter(
        parser_version=PARSER_VERSION,
        scope_key=snapshot_scope_key(hidden_keywords),
    ).first()


def mark_stats_stale() -> None:
    NormalizationStatsSnapshot.objects.update(is_stale=True)


def refresh_stats_snapshot(*, hidden_keywords: list[str] | None = None) -> NormalizationStatsSnapshot:
    hidden_keywords = hidden_keywords or []
    parsed_queryset = apply_hidden_product_keywords(
        ParsedSupplierProduct.objects.all(),
        hidden_keywords,
        fields=PARSED_PRODUCT_HIDDEN_FIELDS,
    )
    non_garbage_queryset = parsed_queryset.exclude(modifiers__contains=[GARBAGE_MODIFIER])
    normal_product_queryset = non_garbage_queryset.exclude(is_set=True)
    unparsed_queryset = apply_hidden_product_keywords(
        SupplierProduct.objects.all(),
        hidden_keywords,
        fields=SUPPLIER_PRODUCT_HIDDEN_FIELDS,
    )

    counts = normal_product_queryset.aggregate(
        parsed_count=Count("id", filter=complete_parse_query()),
        low_confidence_count=Count("id", filter=Q(confidence__lt=75)),
        missing_brand_count=Count("id", filter=Q(normalized_brand__isnull=True)),
        missing_name_count=Count("id", filter=Q(product_name_text="")),
        missing_concentration_count=Count("id", filter=Q(concentration="")),
        missing_size_count=Count("id", filter=Q(size_ml__isnull=True)),
        modifier_count=Count("id", filter=~Q(modifiers=[])),
        tester_sample_count=Count(
            "id",
            filter=Q(is_tester=True) | Q(is_sample=True) | Q(is_travel=True),
        ),
    )
    counts["garbage_count"] = parsed_queryset.filter(modifiers__contains=[GARBAGE_MODIFIER]).count()
    counts["set_count"] = non_garbage_queryset.filter(is_set=True).count()
    counts["unparsed_count"] = unparsed_queryset.filter(assistant_parse__isnull=True).count()
    counts["recent_parse_ids"] = list(
        normal_product_queryset.filter(complete_parse_query())
        .order_by("-updated_at")
        .values_list("id", flat=True)[:20]
    )

    snapshot, _ = NormalizationStatsSnapshot.objects.update_or_create(
        parser_version=PARSER_VERSION,
        scope_key=snapshot_scope_key(hidden_keywords),
        defaults={
            **counts,
            "hidden_keywords_hash": hidden_keywords_hash(hidden_keywords),
            "hidden_keywords": hidden_keywords,
            "generated_at": timezone.now(),
            "is_stale": False,
        },
    )
    return snapshot
