from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import reverse

from prices import models
from prices.services.product_filters import (
    parse_exclude_terms,
    resolve_supplier_exclude_terms,
    serialize_supplier_filter_ids,
    supplier_filter_ids_from_request,
)
from prices.services.product_visibility import apply_hidden_product_keywords


@dataclass(frozen=True)
class ProductLinkingSourceResolution:
    supplier_product: object | None
    error: str
    status_code: int


@dataclass(frozen=True)
class ProductLinkingSearchPayload:
    payload: dict
    status_code: int


def normalize_link_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", (value or "")).lower()
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def link_tokens(value: str) -> list[str]:
    text = normalize_link_text(value)
    if not text:
        return []
    return [token for token in text.split(" ") if len(token) >= 2]


def split_link_search_terms(value: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", value or "") if token]


def parse_product_linking_apply_id(raw_value: str) -> int | None:
    try:
        return int((raw_value or "").strip())
    except (TypeError, ValueError):
        return None


def build_product_linking_list_context(
    request,
    *,
    supplier_product_manager=None,
    supplier_manager=None,
    page_size: int = 50,
    paginator_class=Paginator,
) -> dict:
    supplier_filter_ids = supplier_filter_ids_from_request(request)
    supplier_filter = serialize_supplier_filter_ids(supplier_filter_ids)
    search_query = request.GET.get("q", "").strip()

    supplier_product_manager = (
        supplier_product_manager or models.SupplierProduct.objects
    )
    supplier_products = supplier_product_manager.select_related("supplier")
    supplier_products = apply_hidden_product_keywords(
        supplier_products,
        parse_exclude_terms(resolve_supplier_exclude_terms(request)),
    )
    if supplier_filter_ids:
        supplier_products = supplier_products.filter(
            supplier_id__in=supplier_filter_ids
        )
    if search_query:
        supplier_products = supplier_products.filter(
            Q(name__icontains=search_query) | Q(supplier_sku__icontains=search_query)
        )
    supplier_products = supplier_products.order_by("name")

    paginator = paginator_class(supplier_products, page_size)
    page = paginator.get_page(request.GET.get("sp_page", "1"))

    supplier_manager = supplier_manager or models.Supplier.objects
    return {
        "supplier_products": page,
        "supplier_filter": supplier_filter,
        "search_query": search_query,
        "supplier_options": supplier_manager.order_by("name"),
    }


def build_product_linking_candidate_payload(
    request,
    supplier_product,
    terms: str,
    *,
    our_product_manager=None,
    supplier_product_manager=None,
    our_serializer=None,
    supplier_serializer=None,
    candidate_query_limit: int = 250,
) -> dict:
    tokens = split_link_search_terms(terms)
    hidden_terms = parse_exclude_terms(resolve_supplier_exclude_terms(request))

    our_product_manager = our_product_manager or models.OurProduct.objects
    supplier_product_manager = (
        supplier_product_manager or models.SupplierProduct.objects
    )
    our_serializer = our_serializer or serialize_our_product_link_candidates
    supplier_serializer = (
        supplier_serializer or serialize_supplier_product_link_candidates
    )

    our_products = our_product_manager.all()
    other_supplier_products = supplier_product_manager.select_related(
        "supplier"
    ).exclude(supplier_id=supplier_product.supplier_id)
    other_supplier_products = apply_hidden_product_keywords(
        other_supplier_products,
        hidden_terms,
    )

    for token in tokens:
        our_products = our_products.filter(
            Q(name__icontains=token)
            | Q(brand__icontains=token)
            | Q(size__icontains=token)
        )
        other_supplier_products = other_supplier_products.filter(
            Q(name__icontains=token) | Q(supplier_sku__icontains=token)
        )

    return {
        "our_products": our_serializer(
            supplier_product,
            our_products.order_by("name")[:candidate_query_limit],
        ),
        "supplier_products": supplier_serializer(
            supplier_product,
            other_supplier_products.order_by("name")[:candidate_query_limit],
        ),
        "source": {
            "id": supplier_product.id,
            "name": supplier_product.name,
            "brand": supplier_product.brand,
            "size": supplier_product.size,
        },
    }


def resolve_product_linking_source(
    raw_supplier_product_id: str,
    *,
    supplier_product_manager=None,
) -> ProductLinkingSourceResolution:
    try:
        supplier_product_id = int((raw_supplier_product_id or "").strip())
    except ValueError:
        return ProductLinkingSourceResolution(
            supplier_product=None,
            error="Invalid supplier product.",
            status_code=400,
        )

    supplier_product_manager = (
        supplier_product_manager or models.SupplierProduct.objects
    )
    supplier_product = (
        supplier_product_manager.select_related("supplier")
        .filter(id=supplier_product_id)
        .first()
    )
    if not supplier_product:
        return ProductLinkingSourceResolution(
            supplier_product=None,
            error="Supplier product not found.",
            status_code=404,
        )
    return ProductLinkingSourceResolution(
        supplier_product=supplier_product,
        error="",
        status_code=200,
    )


def build_product_linking_search_payload(
    request,
    *,
    source_resolver=resolve_product_linking_source,
    candidate_payload_builder=build_product_linking_candidate_payload,
) -> ProductLinkingSearchPayload:
    supplier_product_id = request.GET.get("supplier_product", "").strip()
    terms = (request.GET.get("terms", "") or request.GET.get("q", "")).strip()
    auto = request.GET.get("auto", "").strip() == "1"
    resolution = source_resolver(supplier_product_id)
    if resolution.error:
        return ProductLinkingSearchPayload(
            payload={"error": resolution.error},
            status_code=resolution.status_code,
        )

    supplier_product = resolution.supplier_product
    if auto and not terms:
        terms = supplier_product.name or ""
    return ProductLinkingSearchPayload(
        payload=candidate_payload_builder(request, supplier_product, terms),
        status_code=200,
    )


def extract_link_size(value: str) -> str:
    text = unicodedata.normalize("NFKC", (value or "")).lower()
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|\u043c\u043b)", text)
    if not match:
        return ""
    number = match.group(1).replace(",", ".")
    return f"{number}ml"


def score_link_candidate(
    source_name: str,
    source_brand: str,
    source_size: str,
    candidate_name: str,
    candidate_brand: str,
    candidate_size: str,
) -> tuple[float, str]:
    source_tokens = set(link_tokens(source_name))
    candidate_tokens = set(link_tokens(candidate_name))
    if not source_tokens or not candidate_tokens:
        return 0.0, "no tokens"

    overlap = len(source_tokens.intersection(candidate_tokens))
    union = len(source_tokens.union(candidate_tokens)) or 1
    token_score = overlap / union

    reasons = [f"tokens {overlap}/{union}"]
    score = token_score * 0.72

    source_brand_normalized = normalize_link_text(source_brand)
    candidate_brand_normalized = normalize_link_text(candidate_brand)
    if source_brand_normalized and candidate_brand_normalized:
        if source_brand_normalized == candidate_brand_normalized:
            score += 0.20
            reasons.append("brand exact")
        elif (
            source_brand_normalized in candidate_brand_normalized
            or candidate_brand_normalized in source_brand_normalized
        ):
            score += 0.10
            reasons.append("brand partial")

    source_size_normalized = extract_link_size(source_size) or extract_link_size(
        source_name
    )
    candidate_size_normalized = extract_link_size(candidate_size) or extract_link_size(
        candidate_name
    )
    if source_size_normalized and candidate_size_normalized:
        if source_size_normalized == candidate_size_normalized:
            score += 0.08
            reasons.append("size exact")
        elif source_size_normalized.removesuffix(
            "ml"
        ) == candidate_size_normalized.removesuffix("ml"):
            score += 0.04
            reasons.append("size near")

    return min(float(score), 1.0), ", ".join(reasons)


def serialize_our_product_link_candidates(
    supplier_product,
    candidates,
    *,
    limit: int = 50,
) -> list[dict]:
    items = []
    for item in candidates:
        score, reason = score_link_candidate(
            supplier_product.name,
            supplier_product.brand,
            supplier_product.size,
            item.name,
            item.brand,
            item.size,
        )
        if score <= 0:
            continue
        items.append(
            {
                "id": item.id,
                "name": item.name,
                "brand": item.brand,
                "size": item.size,
                "score": round(score * 100, 1),
                "reason": reason,
            }
        )
    items.sort(key=lambda candidate: candidate["score"], reverse=True)
    return items[:limit]


def serialize_supplier_product_link_candidates(
    supplier_product,
    candidates,
    *,
    limit: int = 50,
) -> list[dict]:
    items = []
    for item in candidates:
        score, reason = score_link_candidate(
            supplier_product.name,
            supplier_product.brand,
            supplier_product.size,
            item.name,
            item.brand,
            item.size,
        )
        if score <= 0:
            continue
        items.append(
            {
                "id": item.id,
                "name": item.name,
                "supplier": item.supplier.name,
                "sku": item.supplier_sku,
                "our_product_id": item.our_product_id,
                "score": round(score * 100, 1),
                "reason": reason,
            }
        )
    items.sort(key=lambda candidate: candidate["score"], reverse=True)
    return items[:limit]


def link_supplier_product_to_our_product(source, our_product) -> None:
    source.our_product = our_product
    source.save(update_fields=["our_product"])


def link_supplier_product_to_supplier_product(
    source,
    target,
    *,
    our_product_manager=None,
) -> None:
    if target.our_product:
        link_supplier_product_to_our_product(source, target.our_product)
        return

    manager = our_product_manager or models.OurProduct.objects
    new_our = manager.create(
        name=target.name,
        brand=target.brand,
        size=target.size,
    )
    target.our_product = new_our
    source.our_product = new_our
    target.save(update_fields=["our_product"])
    source.save(update_fields=["our_product"])


def run_product_linking_apply_action(
    post_data,
    *,
    supplier_product_getter=None,
    our_product_getter=None,
    link_our_func=link_supplier_product_to_our_product,
    link_supplier_func=link_supplier_product_to_supplier_product,
) -> str:
    redirect_url = reverse("prices:product_linking")
    source_id = parse_product_linking_apply_id(post_data.get("source_id", ""))
    if source_id is None:
        return redirect_url

    if supplier_product_getter is None:

        def supplier_product_getter(product_id):
            return get_object_or_404(models.SupplierProduct, id=product_id)

    if our_product_getter is None:

        def our_product_getter(product_id):
            return get_object_or_404(models.OurProduct, id=product_id)

    source = supplier_product_getter(source_id)
    target_our_raw = post_data.get("target_our", "").strip()
    target_supplier_raw = post_data.get("target_supplier", "").strip()
    if target_our_raw:
        target_our_id = parse_product_linking_apply_id(target_our_raw)
        if target_our_id is None:
            return redirect_url
        link_our_func(source, our_product_getter(target_our_id))
    elif target_supplier_raw:
        target_supplier_id = parse_product_linking_apply_id(target_supplier_raw)
        if target_supplier_id is None:
            return redirect_url
        link_supplier_func(source, supplier_product_getter(target_supplier_id))
    return redirect_url
