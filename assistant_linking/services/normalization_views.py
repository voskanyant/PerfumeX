from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from assistant_linking import models
from assistant_linking.services.garbage import GARBAGE_MODIFIER
from assistant_linking.services.normalization_stats import (
    complete_parse_query,
    empty_stats,
    get_stats_snapshot,
    refresh_stats_snapshot,
    snapshot_to_stats,
)
from assistant_linking.services.normalizer import parse_supplier_product, save_parse
from prices.models import SupplierProduct
from prices.services.job_queue import enqueue_management_command
from prices.services.product_visibility import apply_hidden_product_keywords


SUPPLIER_PRODUCT_HIDDEN_FIELDS = ("name", "brand", "supplier_sku")
PARSED_PRODUCT_HIDDEN_FIELDS = (
    "supplier_product__name",
    "supplier_product__brand",
    "supplier_product__supplier_sku",
)
COMPLETE_PARSED_ORDER = (
    "-updated_at",
    "supplier_product__supplier__name",
    "supplier_product__name",
)


@dataclass(frozen=True)
class ParseUnparsedProductsResult:
    success: bool
    message_level: str
    message: str


def normalization_dashboard_stats(
    request, hidden_keywords: list[str]
) -> dict[str, object]:
    if request.GET.get("refresh") == "1":
        return snapshot_to_stats(
            refresh_stats_snapshot(hidden_keywords=hidden_keywords)
        )
    snapshot = get_stats_snapshot(hidden_keywords=hidden_keywords)
    return snapshot_to_stats(snapshot) if snapshot else empty_stats()


def build_normalization_dashboard_context(
    request,
    *,
    hidden_keywords: list[str],
    stats_builder=normalization_dashboard_stats,
    parsed_model=models.ParsedSupplierProduct,
) -> dict:
    stats = stats_builder(request, hidden_keywords)
    recent_ids = stats.get("recent_ids") or []
    recent = (
        parsed_model.objects.select_related("supplier_product", "normalized_brand")
        .filter(id__in=recent_ids)
        .order_by("-updated_at")[:20]
    )
    return {
        **{key: value for key, value in stats.items() if key != "recent_ids"},
        "recent": recent,
        "hidden_keywords_active": bool(hidden_keywords),
    }


def hide_supplier_products(queryset, hidden_keywords: list[str]):
    return apply_hidden_product_keywords(
        queryset,
        hidden_keywords,
        fields=SUPPLIER_PRODUCT_HIDDEN_FIELDS,
    )


def hide_parsed_products(queryset, hidden_keywords: list[str]):
    return apply_hidden_product_keywords(
        queryset,
        hidden_keywords,
        fields=PARSED_PRODUCT_HIDDEN_FIELDS,
    )


def apply_parsed_search(queryset, query):
    if not query:
        return queryset
    return queryset.filter(
        Q(supplier_product__supplier__name__icontains=query)
        | Q(supplier_product__name__icontains=query)
        | Q(supplier_product__supplier_sku__icontains=query)
        | Q(supplier_product__brand__icontains=query)
        | Q(normalized_brand__name__icontains=query)
        | Q(detected_brand_text__icontains=query)
        | Q(product_name_text__icontains=query)
        | Q(concentration__icontains=query)
    )


def parsed_supplier_product_queryset(parsed_model=models.ParsedSupplierProduct):
    return parsed_model.objects.select_related(
        "supplier_product",
        "supplier_product__supplier",
        "normalized_brand",
    )


def exclude_garbage_parses(queryset):
    return queryset.exclude(modifiers__contains=[GARBAGE_MODIFIER])


def exclude_bag_parses(queryset):
    return queryset.exclude(modifiers__contains=[models.BAG_MODIFIER]).exclude(
        variant_type=models.BAG_MODIFIER
    )


def exclude_cosmetic_parses(queryset):
    return queryset.exclude(
        modifiers__contains=[models.COSMETIC_PUDRE_MODIFIER]
    ).exclude(variant_type="poudre")


def exclude_deodorant_parses(queryset):
    return queryset.exclude(modifiers__contains=[models.DEODORANT_MODIFIER]).exclude(
        variant_type=models.DEODORANT_MODIFIER
    )


def exclude_decant_parses(queryset):
    return queryset.exclude(modifiers__contains=[models.DECANT_MODIFIER]).exclude(
        variant_type=models.DECANT_MODIFIER
    )


def exclude_vintage_parses(queryset):
    return queryset.exclude(modifiers__contains=[models.VINTAGE_MODIFIER]).exclude(
        variant_type=models.VINTAGE_MODIFIER
    )


def exclude_atomizer_parses(queryset):
    return queryset.exclude(modifiers__contains=[models.ATOMIZER_MODIFIER]).exclude(
        variant_type=models.ATOMIZER_MODIFIER
    )


def exclude_non_perfume_parses(queryset):
    return exclude_atomizer_parses(
        exclude_vintage_parses(
            exclude_decant_parses(
                exclude_deodorant_parses(
                    exclude_cosmetic_parses(exclude_bag_parses(queryset))
                )
            )
        )
    )


def exclude_manual_review_parses(queryset):
    return queryset.exclude(modifiers__contains=[models.MANUAL_REVIEW_MODIFIER])


def normal_perfume_parses(queryset):
    return exclude_manual_review_parses(exclude_non_perfume_parses(queryset))


def exclude_set_parses(queryset):
    return queryset.exclude(is_set=True)


def bag_parses(queryset):
    return queryset.filter(
        Q(modifiers__contains=[models.BAG_MODIFIER])
        | Q(variant_type=models.BAG_MODIFIER)
    )


def cosmetic_parses(queryset):
    return queryset.filter(
        Q(modifiers__contains=[models.COSMETIC_PUDRE_MODIFIER])
        | Q(variant_type="poudre")
    )


def deodorant_parses(queryset):
    return queryset.filter(
        Q(modifiers__contains=[models.DEODORANT_MODIFIER])
        | Q(variant_type=models.DEODORANT_MODIFIER)
    )


def decant_parses(queryset):
    return queryset.filter(
        Q(modifiers__contains=[models.DECANT_MODIFIER])
        | Q(variant_type=models.DECANT_MODIFIER)
    )


def vintage_parses(queryset):
    return queryset.filter(
        Q(modifiers__contains=[models.VINTAGE_MODIFIER])
        | Q(variant_type=models.VINTAGE_MODIFIER)
    )


def atomizer_parses(queryset):
    return queryset.filter(
        Q(modifiers__contains=[models.ATOMIZER_MODIFIER])
        | Q(variant_type=models.ATOMIZER_MODIFIER)
    )


def manual_review_parses(queryset):
    return queryset.filter(modifiers__contains=[models.MANUAL_REVIEW_MODIFIER])


def complete_parses(queryset):
    return queryset.filter(complete_parse_query())


def _normal_issue_queryset(
    queryset,
    query: str,
    hidden_keywords: list[str],
    *,
    hider=hide_parsed_products,
):
    queryset = apply_parsed_search(queryset, (query or "").strip())
    queryset = normal_perfume_parses(
        exclude_set_parses(exclude_garbage_parses(hider(queryset, hidden_keywords)))
    )
    return queryset.order_by("supplier_product__name")


def _category_issue_queryset(
    queryset,
    query: str,
    hidden_keywords: list[str],
    *,
    hider=hide_parsed_products,
):
    queryset = exclude_garbage_parses(hider(queryset, hidden_keywords))
    queryset = apply_parsed_search(queryset, (query or "").strip())
    return queryset.order_by(
        "supplier_product__supplier__name", "supplier_product__name"
    )


def build_unparsed_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    supplier_product_model=SupplierProduct,
    hider=hide_supplier_products,
):
    queryset = supplier_product_model.objects.select_related("supplier").filter(
        assistant_parse__isnull=True
    )
    query = (query or "").strip()
    if query:
        queryset = queryset.filter(
            Q(supplier__name__icontains=query)
            | Q(name__icontains=query)
            | Q(brand__icontains=query)
            | Q(size__icontains=query)
            | Q(supplier_sku__icontains=query)
        )
    queryset = hider(queryset, hidden_keywords)
    return queryset.order_by("supplier__name", "name")


def attach_unparsed_parse_previews(
    products,
    *,
    parse_preview_builder=parse_supplier_product,
):
    for product in products:
        product.parsed_preview = parse_preview_builder(product)
    return products


def refresh_visible_unparsed_context(
    context,
    *,
    force_refresh=False,
    preview=False,
    supplier_product_model=SupplierProduct,
    parse_saver=save_parse,
    parse_preview_builder=parse_supplier_product,
):
    context["allow_refresh_visible"] = True
    visible_products = list(context.get("products", []))
    if not force_refresh and not preview:
        context["unparsed_preview_deferred"] = True
        return context
    if not force_refresh:
        attach_unparsed_parse_previews(
            visible_products,
            parse_preview_builder=parse_preview_builder,
        )
        context["preview_visible"] = True
        return context

    refreshed_count = 0
    if visible_products:
        for product in visible_products:
            parse_saver(product)
            refreshed_count += 1
        visible_ids = [product.pk for product in visible_products]
        still_unparsed_ids = set(
            supplier_product_model.objects.filter(
                pk__in=visible_ids,
                assistant_parse__isnull=True,
            ).values_list("pk", flat=True)
        )
        visible_products = [
            product for product in visible_products if product.pk in still_unparsed_ids
        ]
        context["products"] = visible_products
        context["object_list"] = visible_products
        if context.get("page_obj"):
            context["page_obj"].object_list = visible_products
    attach_unparsed_parse_previews(
        visible_products,
        parse_preview_builder=parse_preview_builder,
    )
    context["refreshed_visible_count"] = refreshed_count
    context["moved_visible_count"] = refreshed_count - len(visible_products)
    return context


def dispatch_parse_unparsed_products(
    *,
    dispatcher=enqueue_management_command,
) -> ParseUnparsedProductsResult:
    try:
        result = dispatcher(
            "reparse_supplier_products",
            only_unparsed=True,
            description="Parse unparsed supplier products",
        )
    except Exception as exc:
        return ParseUnparsedProductsResult(
            success=False,
            message_level="error",
            message=f"Could not start unparsed product parsing: {exc}",
        )

    if result.queued:
        return ParseUnparsedProductsResult(
            success=True,
            message_level="success",
            message=(
                "Unparsed product parsing was queued. Refresh stats after the job "
                "finishes to update dashboard counts."
            ),
        )
    return ParseUnparsedProductsResult(
        success=True,
        message_level="success",
        message="Unparsed product parsing completed. Refresh stats to update dashboard counts.",
    )


def build_low_confidence_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_model.objects.select_related(
        "supplier_product",
        "supplier_product__supplier",
        "normalized_brand",
    ).filter(confidence__lt=75)
    queryset = apply_parsed_search(queryset, (query or "").strip())
    queryset = normal_perfume_parses(
        exclude_set_parses(exclude_garbage_parses(hider(queryset, hidden_keywords)))
    )
    return queryset.order_by(
        "confidence", "supplier_product__supplier__name", "supplier_product__name"
    )


def build_missing_brand_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model).filter(
        normalized_brand__isnull=True
    )
    return _normal_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_missing_name_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model).filter(
        product_name_text=""
    )
    return _normal_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_missing_concentration_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model).filter(concentration="")
    return _normal_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_missing_size_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model).filter(
        size_ml__isnull=True
    )
    return _normal_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_tester_sample_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model).filter(
        Q(is_tester=True) | Q(is_sample=True) | Q(is_travel=True),
        is_set=False,
    )
    queryset = apply_parsed_search(queryset, (query or "").strip())
    queryset = normal_perfume_parses(
        exclude_garbage_parses(hider(queryset, hidden_keywords))
    )
    return queryset.order_by("supplier_product__name")


def build_set_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model).filter(is_set=True)
    queryset = apply_parsed_search(queryset, (query or "").strip())
    queryset = exclude_garbage_parses(hider(queryset, hidden_keywords))
    return queryset.order_by(
        "supplier_product__supplier__name", "supplier_product__name"
    )


def build_bag_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = bag_parses(parsed_supplier_product_queryset(parsed_model))
    return _category_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_cosmetic_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = cosmetic_parses(parsed_supplier_product_queryset(parsed_model))
    return _category_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_deodorant_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = deodorant_parses(parsed_supplier_product_queryset(parsed_model))
    return _category_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_decant_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = decant_parses(parsed_supplier_product_queryset(parsed_model))
    return _category_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_vintage_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = vintage_parses(parsed_supplier_product_queryset(parsed_model))
    return _category_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_atomizer_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = atomizer_parses(parsed_supplier_product_queryset(parsed_model))
    return _category_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_manual_review_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = manual_review_parses(parsed_supplier_product_queryset(parsed_model))
    return _category_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_modifier_conflict_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model).exclude(modifiers=[])
    return _normal_issue_queryset(queryset, query, hidden_keywords, hider=hider)


def build_garbage_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model).filter(
        modifiers__contains=[GARBAGE_MODIFIER]
    )
    queryset = apply_parsed_search(queryset, (query or "").strip())
    queryset = hider(queryset, hidden_keywords)
    return queryset.order_by(
        "supplier_product__supplier__name", "supplier_product__name"
    )


def build_complete_parsed_queryset(
    query: str,
    hidden_keywords: list[str],
    *,
    parsed_model=models.ParsedSupplierProduct,
    hider=hide_parsed_products,
):
    queryset = parsed_supplier_product_queryset(parsed_model)
    queryset = hider(queryset, hidden_keywords)
    queryset = apply_parsed_search(queryset, (query or "").strip())
    queryset = complete_parses(normal_perfume_parses(exclude_garbage_parses(queryset)))
    return queryset.order_by(*COMPLETE_PARSED_ORDER)


def build_complete_parsed_id_queryset(
    parsed_ids,
    *,
    parsed_model=models.ParsedSupplierProduct,
):
    queryset = parsed_supplier_product_queryset(parsed_model).filter(pk__in=parsed_ids)
    queryset = complete_parses(normal_perfume_parses(exclude_garbage_parses(queryset)))
    return queryset.order_by(*COMPLETE_PARSED_ORDER)


def refresh_visible_parsed_context(
    context,
    *,
    force_refresh,
    parse_saver=save_parse,
    parsed_id_queryset_builder=build_complete_parsed_id_queryset,
):
    context["allow_refresh_visible"] = True
    visible_parses = list(context.get("parses", []))
    refreshed_count = 0
    refreshed_parses = []
    for parsed in visible_parses:
        if force_refresh:
            refreshed_parses.append(parse_saver(parsed.supplier_product))
            refreshed_count += 1
        else:
            refreshed_parses.append(parsed)
    if refreshed_count:
        refreshed_parses = list(
            parsed_id_queryset_builder([parsed.pk for parsed in refreshed_parses])
        )
        context["parses"] = refreshed_parses
        context["object_list"] = refreshed_parses
        context["refreshed_visible"] = True
        context["refreshed_visible_count"] = refreshed_count
        if context.get("page_obj"):
            context["page_obj"].object_list = refreshed_parses
    return context
