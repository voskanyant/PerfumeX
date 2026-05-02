from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from assistant_linking.models import ParsedSupplierProduct
from assistant_linking.models import FragranticaProduct
from assistant_linking.utils.text import normalize_alias_value
from catalog.models import Brand as CatalogBrand
from catalog.models import Perfume as CatalogPerfume
from catalog.models import PerfumeVariant as CatalogPerfumeVariant
from prices import models
from prices.services.product_filters import (
    parse_exclude_terms,
    resolve_supplier_exclude_terms,
)
from prices.services.product_visibility import apply_hidden_product_keywords


FRAGRANTICA_REVIEW_STATUSES = {
    "all",
    "linked",
    "supplier_evidence",
    "catalog_only",
    "collection_review",
}
OUR_PRODUCT_CATALOG_TABS = {"products", "brands", "collections", "concentrations"}
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

    return {
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
    brand_manager=None,
    perfume_manager=None,
    parsed_product_manager=None,
    paginator_class=Paginator,
    page_size: int = 50,
) -> dict:
    brand_id = normalize_fragrantica_review_brand_id(request.GET.get("brand") or "")
    search_query = request.GET.get("q", "").strip()
    status_filter = normalize_fragrantica_review_status(
        request.GET.get("status", "all")
    )

    perfumes = list(
        build_fragrantica_review_perfume_queryset(
            brand_id,
            search_query,
            perfume_manager=perfume_manager,
        )
    )
    brand_ids = {perfume.brand_id for perfume in perfumes}
    if brand_id:
        brand_ids.add(int(brand_id))
    evidence_by_name = (
        build_parsed_supplier_evidence_by_name(
            brand_ids,
            parsed_product_manager=parsed_product_manager,
        )
        if brand_ids
        else {}
    )
    catalogue_keys = {
        (perfume.brand_id, catalogue_review_name_key(perfume.name))
        for perfume in perfumes
        if catalogue_review_name_key(perfume.name)
    }

    rows = []
    status_counts = defaultdict(int)
    for perfume in perfumes:
        evidence = evidence_by_name.get(
            (perfume.brand_id, catalogue_review_name_key(perfume.name)), []
        )
        status_key, status_label = fragrantica_review_row_status(perfume, evidence)
        status_counts[status_key] += 1
        if status_filter != "all" and status_filter != status_key:
            continue
        rows.append(
            {
                "perfume": perfume,
                "evidence": evidence[:5],
                "evidence_count": len(evidence),
                "status_key": status_key,
                "status_label": status_label,
                "collection_names": sorted(
                    {item.collection_name for item in evidence if item.collection_name}
                ),
            }
        )

    missing_supplier_rows = build_missing_supplier_rows(
        evidence_by_name,
        catalogue_keys,
    )

    paginator = paginator_class(rows, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)

    brand_manager = brand_manager or CatalogBrand.objects
    return {
        "brands": brand_manager.annotate(perfume_count=Count("perfumes")).order_by(
            "name"
        ),
        "selected_brand_id": str(brand_id),
        "search_query": search_query,
        "status_filter": status_filter,
        "status_counts": dict(status_counts),
        "total_count": len(perfumes),
        "filtered_count": len(rows),
        "rows": page_obj.object_list,
        "missing_supplier_rows": missing_supplier_rows[:100],
        "missing_supplier_count": len(missing_supplier_rows),
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": paginator.num_pages > 1,
        "query_string": query_without_page.urlencode(),
        **build_fragrantica_staging_context(
            request,
            perfume_manager=perfume_manager,
        ),
    }


def fragrantica_identity_key(brand_name: str, perfume_name: str) -> tuple[str, str]:
    return (
        normalize_alias_value(brand_name or "").replace("&", "and"),
        normalize_alias_value(perfume_name or "").replace("&", "and"),
    )


def build_fragrantica_candidate_map(fragrantica_rows, *, perfume_manager=None) -> dict:
    perfume_manager = perfume_manager or CatalogPerfume.objects
    keys = {
        fragrantica_identity_key(row.brand_name, row.name)
        for row in fragrantica_rows
        if row.brand_name and row.name
    }
    if not keys:
        return {}
    brand_names = {brand_name for brand_name, _name in keys}
    name_keys = {name for _brand_name, name in keys}
    candidates = perfume_manager.select_related("brand").filter(name__isnull=False)
    candidates = candidates.filter(brand__name__isnull=False)
    candidate_map = {}
    for perfume in candidates:
        key = fragrantica_identity_key(perfume.brand.name, perfume.name)
        if key[0] in brand_names and key[1] in name_keys and key in keys:
            candidate_map.setdefault(key, perfume)
    return candidate_map


def build_fragrantica_staging_context(
    request,
    *,
    fragrantica_manager=None,
    perfume_manager=None,
    row_limit: int = 25,
) -> dict:
    fragrantica_manager = fragrantica_manager or FragranticaProduct.objects
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
        brand = CatalogBrand.objects.filter(pk=brand_id).first()
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


def apply_fragrantica_identity_to_perfume(source, perfume) -> list[str]:
    changed_fields = []
    if source.name and perfume.name != source.name:
        perfume.name = source.name
        changed_fields.append("name")
    if source.collection_name and perfume.collection_name != source.collection_name:
        perfume.collection_name = source.collection_name
        changed_fields.append("collection_name")
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
    source.matched_perfume = perfume
    source.match_status = FragranticaProduct.STATUS_LINKED
    source.save(update_fields=["matched_perfume", "match_status", "updated_at"])
    changed_fields = apply_fragrantica_identity_to_perfume(source, perfume)
    preserved_note = " Concentration and variants were preserved."
    changed_note = (
        f" Updated catalogue fields: {', '.join(changed_fields[:-1])}."
        if changed_fields
        else " Catalogue identity already matched."
    )
    return FragranticaCatalogueLinkResult(
        "success",
        f"Linked Fragrantica row to {perfume.brand.name} / {perfume.name}."
        f"{changed_note}{preserved_note}",
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
