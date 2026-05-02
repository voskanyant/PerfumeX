from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse_lazy

from assistant_core import models
from assistant_linking.services.parser_rules import (
    PARSER_RULE_KIND_OPTIONS,
    PARSER_RULE_KINDS,
)

SECTION_GARBAGE_KEYWORDS = "garbage_keywords"
SECTION_PARSER_TERMS = "parser_terms"
SECTION_GLOBAL_RULES = "global_rules"
SECTION_SUPPLIER_RULES = "supplier_rules"
SECTION_NOTES = "notes"
SECTION_BRAND_ALIASES = "brand_aliases"
SECTION_PRODUCT_ALIASES = "product_aliases"
SECTION_CONCENTRATION_ALIASES = "concentration_aliases"
SECTION_DECISIONS = "decisions"
SECTION_CHOICES = {
    SECTION_GLOBAL_RULES,
    SECTION_SUPPLIER_RULES,
    SECTION_NOTES,
    SECTION_BRAND_ALIASES,
    SECTION_PRODUCT_ALIASES,
    SECTION_CONCENTRATION_ALIASES,
    SECTION_DECISIONS,
    SECTION_GARBAGE_KEYWORDS,
    SECTION_PARSER_TERMS,
}
ALIAS_SECTION_BRANDS = "brands"
ALIAS_SECTION_PRODUCTS = "products"
ALIAS_SECTION_CONCENTRATIONS = "concentrations"
ALIAS_SECTION_CHOICES = {
    ALIAS_SECTION_BRANDS,
    ALIAS_SECTION_PRODUCTS,
    ALIAS_SECTION_CONCENTRATIONS,
}


def active_knowledge_section(query_params, *, default_section: str) -> str:
    section = query_params.get("section", default_section).strip()
    return section if section in SECTION_CHOICES else default_section


def _filter_status(query_params, queryset, field_name="active"):
    status = query_params.get("status", "active").strip()
    if status == "active":
        queryset = queryset.filter(**{field_name: True})
    elif status == "inactive":
        queryset = queryset.filter(**{field_name: False})
    return queryset, status


def _scope_filter(query_params, queryset):
    scope = query_params.get("scope", "all").strip()
    if scope == "global":
        queryset = queryset.filter(supplier__isnull=True)
    elif scope == "supplier":
        queryset = queryset.filter(supplier__isnull=False)
    return queryset, scope


def queryset_for_knowledge_section(query_params, section: str, query: str):
    from assistant_linking.models import (
        BrandAlias,
        ConcentrationAlias,
        ManualLinkDecision,
        ProductAlias,
    )

    if section in {
        SECTION_GLOBAL_RULES,
        SECTION_GARBAGE_KEYWORDS,
        SECTION_PARSER_TERMS,
    }:
        queryset = models.GlobalRule.objects.order_by("priority", "title")
        if section == SECTION_GARBAGE_KEYWORDS:
            queryset = queryset.filter(
                rule_kind__in=("garbage_keyword", "exclude_keyword")
            )
        elif section == SECTION_PARSER_TERMS:
            queryset = queryset.filter(rule_kind__in=PARSER_RULE_KINDS)
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(rule_kind__icontains=query)
                | Q(rule_text__icontains=query)
                | Q(scope_type__icontains=query)
                | Q(scope_value__icontains=query)
            )
        queryset, status = _filter_status(query_params, queryset)
        return queryset, {"status": status, "scope": "all"}

    if section == SECTION_SUPPLIER_RULES:
        queryset = models.SupplierRule.objects.select_related(
            "supplier", "brand"
        ).order_by("supplier__name", "priority", "title")
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(rule_kind__icontains=query)
                | Q(rule_text__icontains=query)
                | Q(applies_to_text__icontains=query)
                | Q(supplier__name__icontains=query)
                | Q(brand__name__icontains=query)
            )
        queryset, status = _filter_status(query_params, queryset)
        return queryset, {"status": status, "scope": "all"}

    if section == SECTION_NOTES:
        queryset = models.KnowledgeNote.objects.select_related(
            "supplier", "brand", "perfume"
        ).order_by("category", "title")
        if query:
            queryset = queryset.filter(
                Q(category__icontains=query)
                | Q(title__icontains=query)
                | Q(content__icontains=query)
                | Q(supplier__name__icontains=query)
                | Q(brand__name__icontains=query)
                | Q(perfume__name__icontains=query)
            )
        queryset, status = _filter_status(query_params, queryset)
        return queryset, {"status": status, "scope": "all"}

    if section == SECTION_PRODUCT_ALIASES:
        queryset = ProductAlias.objects.select_related(
            "brand", "perfume", "supplier"
        ).order_by("supplier__name", "priority", "alias_text")
        if query:
            queryset = queryset.filter(
                Q(alias_text__icontains=query)
                | Q(canonical_text__icontains=query)
                | Q(collection_name__icontains=query)
                | Q(excluded_terms__icontains=query)
                | Q(concentration__icontains=query)
                | Q(audience__icontains=query)
                | Q(brand__name__icontains=query)
                | Q(perfume__name__icontains=query)
                | Q(supplier__name__icontains=query)
            )
        queryset, scope = _scope_filter(query_params, queryset)
        queryset, status = _filter_status(query_params, queryset)
        return queryset, {"status": status, "scope": scope}

    if section == SECTION_CONCENTRATION_ALIASES:
        queryset = ConcentrationAlias.objects.select_related("supplier").order_by(
            "supplier__name", "priority", "alias_text"
        )
        if query:
            queryset = queryset.filter(
                Q(alias_text__icontains=query)
                | Q(normalized_alias__icontains=query)
                | Q(concentration__icontains=query)
                | Q(supplier__name__icontains=query)
            )
        queryset, scope = _scope_filter(query_params, queryset)
        queryset, status = _filter_status(query_params, queryset)
        return queryset, {"status": status, "scope": scope}

    if section == SECTION_DECISIONS:
        queryset = ManualLinkDecision.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "perfume",
            "variant",
        ).order_by("-created_at")
        if query:
            queryset = queryset.filter(
                Q(supplier_product__name__icontains=query)
                | Q(supplier_product__supplier__name__icontains=query)
                | Q(perfume__name__icontains=query)
                | Q(perfume__brand__name__icontains=query)
                | Q(reason__icontains=query)
                | Q(decision_type__icontains=query)
            )
        return queryset, {"status": "all", "scope": "all"}

    queryset = BrandAlias.objects.select_related("brand", "supplier").order_by(
        "supplier__name", "priority", "alias_text"
    )
    if query:
        queryset = queryset.filter(
            Q(alias_text__icontains=query)
            | Q(normalized_alias__icontains=query)
            | Q(brand__name__icontains=query)
            | Q(supplier__name__icontains=query)
        )
    queryset, scope = _scope_filter(query_params, queryset)
    queryset, status = _filter_status(query_params, queryset)
    return queryset, {"status": status, "scope": scope}


def build_knowledge_context(
    query_params,
    *,
    default_section: str = SECTION_BRAND_ALIASES,
    paginate_by: int = 50,
) -> dict:
    from assistant_linking.models import (
        BrandAlias,
        ConcentrationAlias,
        ManualLinkDecision,
        ProductAlias,
    )

    section = active_knowledge_section(query_params, default_section=default_section)
    query = query_params.get("q", "").strip()
    queryset, filters = queryset_for_knowledge_section(query_params, section, query)
    page_obj = Paginator(queryset, paginate_by).get_page(query_params.get("page") or 1)

    sections = [
        {
            "key": SECTION_BRAND_ALIASES,
            "label": "Brand aliases",
            "count": BrandAlias.objects.count(),
        },
        {
            "key": SECTION_PRODUCT_ALIASES,
            "label": "Product aliases",
            "count": ProductAlias.objects.count(),
        },
        {
            "key": SECTION_CONCENTRATION_ALIASES,
            "label": "Concentration aliases",
            "count": ConcentrationAlias.objects.count(),
        },
        {
            "key": SECTION_GARBAGE_KEYWORDS,
            "label": "Garbage keywords",
            "count": models.GlobalRule.objects.filter(
                rule_kind__in=("garbage_keyword", "exclude_keyword")
            ).count(),
        },
        {
            "key": SECTION_PARSER_TERMS,
            "label": "Parser terms",
            "count": models.GlobalRule.objects.filter(
                rule_kind__in=PARSER_RULE_KINDS
            ).count(),
        },
        {
            "key": SECTION_GLOBAL_RULES,
            "label": "Global rules",
            "count": models.GlobalRule.objects.count(),
        },
        {
            "key": SECTION_SUPPLIER_RULES,
            "label": "Supplier rules",
            "count": models.SupplierRule.objects.count(),
        },
        {
            "key": SECTION_NOTES,
            "label": "Notes",
            "count": models.KnowledgeNote.objects.count(),
        },
        {
            "key": SECTION_DECISIONS,
            "label": "Manual decisions",
            "count": ManualLinkDecision.objects.count(),
        },
    ]

    return {
        "active_section": section,
        "sections": sections,
        "query": query,
        "scope": filters["scope"],
        "status": filters["status"],
        "page_obj": page_obj,
        "items": page_obj.object_list,
        "concentration_alias_count": ConcentrationAlias.objects.count(),
        "parser_rule_kind_options": PARSER_RULE_KIND_OPTIONS,
    }


def active_alias_section(query_params, *, default_section: str = ALIAS_SECTION_BRANDS):
    section = query_params.get("section", default_section).strip()
    return section if section in ALIAS_SECTION_CHOICES else default_section


def alias_queryset_for_section(section: str, query: str):
    from assistant_linking.models import (
        BrandAlias,
        ConcentrationAlias,
        ProductAlias,
    )

    if section == ALIAS_SECTION_PRODUCTS:
        queryset = ProductAlias.objects.select_related(
            "brand", "perfume", "supplier"
        ).order_by("supplier__name", "priority", "alias_text")
        if query:
            queryset = queryset.filter(
                Q(alias_text__icontains=query)
                | Q(canonical_text__icontains=query)
                | Q(collection_name__icontains=query)
                | Q(excluded_terms__icontains=query)
                | Q(concentration__icontains=query)
                | Q(audience__icontains=query)
                | Q(brand__name__icontains=query)
                | Q(perfume__name__icontains=query)
                | Q(supplier__name__icontains=query)
            )
        return queryset

    if section == ALIAS_SECTION_CONCENTRATIONS:
        queryset = ConcentrationAlias.objects.select_related("supplier").order_by(
            "supplier__name", "priority", "alias_text"
        )
        if query:
            queryset = queryset.filter(
                Q(alias_text__icontains=query)
                | Q(normalized_alias__icontains=query)
                | Q(concentration__icontains=query)
                | Q(supplier__name__icontains=query)
            )
        return queryset

    queryset = BrandAlias.objects.select_related("brand", "supplier").order_by(
        "supplier__name", "priority", "alias_text"
    )
    if query:
        queryset = queryset.filter(
            Q(alias_text__icontains=query)
            | Q(normalized_alias__icontains=query)
            | Q(brand__name__icontains=query)
            | Q(supplier__name__icontains=query)
        )
    return queryset


def build_aliases_context(query_params, *, paginate_by: int = 50) -> dict:
    from assistant_linking.models import (
        BrandAlias,
        ConcentrationAlias,
        ProductAlias,
    )

    section = active_alias_section(query_params)
    query = query_params.get("q", "").strip()
    queryset = alias_queryset_for_section(section, query)
    queryset, scope = _scope_filter(query_params, queryset)
    queryset, status = _filter_status(query_params, queryset)
    page_obj = Paginator(queryset, paginate_by).get_page(query_params.get("page") or 1)

    sections = [
        {
            "key": ALIAS_SECTION_BRANDS,
            "label": "Brand aliases",
            "count": BrandAlias.objects.count(),
            "create_url": reverse_lazy("assistant_core:brand_alias_create"),
        },
        {
            "key": ALIAS_SECTION_PRODUCTS,
            "label": "Product aliases",
            "count": ProductAlias.objects.count(),
            "create_url": reverse_lazy("assistant_core:product_alias_create"),
        },
        {
            "key": ALIAS_SECTION_CONCENTRATIONS,
            "label": "Concentration aliases",
            "count": ConcentrationAlias.objects.count(),
            "create_url": reverse_lazy("assistant_core:concentration_alias_create"),
        },
    ]

    return {
        "active_section": section,
        "sections": sections,
        "query": query,
        "scope": scope,
        "status": status,
        "page_obj": page_obj,
        "items": page_obj.object_list,
        "create_url": next(
            item["create_url"] for item in sections if item["key"] == section
        ),
    }
