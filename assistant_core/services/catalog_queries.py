from __future__ import annotations

from django.db.models import Count, Q

from catalog.models import Brand, Perfume, PerfumeVariant


def build_catalog_form_context(
    *,
    perfume_model=Perfume,
    variant_model=PerfumeVariant,
) -> dict:
    return {
        "concentrations": perfume_model.objects.exclude(concentration="")
        .values_list("concentration", flat=True)
        .distinct()
        .order_by("concentration"),
        "audiences": perfume_model.objects.exclude(audience="")
        .values_list("audience", flat=True)
        .distinct()
        .order_by("audience"),
        "packagings": variant_model.objects.exclude(packaging="")
        .values_list("packaging", flat=True)
        .distinct()
        .order_by("packaging"),
        "variant_types": variant_model.objects.exclude(variant_type="")
        .values_list("variant_type", flat=True)
        .distinct()
        .order_by("variant_type"),
    }


def build_catalog_brand_queryset(query: str, *, brand_model=Brand):
    queryset = brand_model.objects.annotate(perfume_count=Count("perfumes"))
    query = (query or "").strip()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query) | Q(country_of_origin__icontains=query)
        )
    return queryset.order_by("name")


def build_catalog_variant_queryset(query: str, *, variant_model=PerfumeVariant):
    queryset = variant_model.objects.select_related("perfume", "perfume__brand")
    query = (query or "").strip()
    if query:
        queryset = queryset.filter(
            Q(perfume__name__icontains=query)
            | Q(perfume__brand__name__icontains=query)
            | Q(packaging__icontains=query)
            | Q(variant_type__icontains=query)
            | Q(ean__icontains=query)
            | Q(sku__icontains=query)
        )
    return queryset.order_by("perfume__brand__name", "perfume__name", "size_ml")
