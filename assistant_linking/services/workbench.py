from __future__ import annotations

from assistant_linking import models
from assistant_linking.services.link_actions import latest_undoable_action
from assistant_linking.services.normalizer import save_parse
from prices.models import SupplierProduct


def find_product_group(product, *, group_model=models.MatchGroup):
    return group_model.objects.filter(items__supplier_product=product).first()


def similar_products_for_group(
    group,
    *,
    supplier_product_model=SupplierProduct,
):
    if not group:
        return supplier_product_model.objects.none()
    return supplier_product_model.objects.filter(
        assistant_group_items__match_group=group
    ).select_related("supplier", "catalog_perfume", "catalog_variant")


def product_link_suggestions(product):
    return product.assistant_link_suggestions.select_related(
        "suggested_perfume",
        "suggested_variant",
    ).order_by("-created_at")[:10]


def same_supplier_products(
    product,
    *,
    supplier_product_model=SupplierProduct,
):
    return (
        supplier_product_model.objects.filter(supplier=product.supplier)
        .exclude(pk=product.pk)
        .order_by("-is_active", "name")[:25]
    )


def build_product_workbench_context(
    *,
    product,
    user,
    parse_saver=save_parse,
    group_finder=find_product_group,
    similar_builder=similar_products_for_group,
    suggestions_builder=product_link_suggestions,
    same_supplier_builder=same_supplier_products,
    latest_action_finder=latest_undoable_action,
):
    parsed = parse_saver(product)
    group = group_finder(product)
    return {
        "parsed": parsed,
        "group": group,
        "similar": similar_builder(group),
        "suggestions": suggestions_builder(product),
        "same_supplier": same_supplier_builder(product),
        "last_link_action": latest_action_finder(user),
    }
