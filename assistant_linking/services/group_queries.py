from __future__ import annotations

from django.db.models import Count, Q

from assistant_linking import models
from assistant_linking.services.link_actions import latest_undoable_action


def group_queue_queryset(
    *,
    status="",
    brand="",
    group_model=models.MatchGroup,
):
    queryset = group_model.objects.select_related(
        "normalized_brand",
        "candidate_perfume",
        "candidate_variant",
    ).annotate(item_count=Count("items"))
    if status:
        queryset = queryset.filter(status=status)
    if brand:
        queryset = queryset.filter(
            Q(normalized_brand__name__icontains=brand)
            | Q(canonical_name__icontains=brand)
        )
    return queryset.order_by("status", "-confidence", "canonical_name")


def group_detail_items(group):
    return group.items.select_related(
        "supplier_product",
        "supplier_product__supplier",
        "parsed_product",
    )


def build_group_detail_context(
    *,
    group,
    user,
    items_builder=group_detail_items,
    latest_action_finder=latest_undoable_action,
):
    return {
        "items": items_builder(group),
        "last_link_action": latest_action_finder(user),
    }
