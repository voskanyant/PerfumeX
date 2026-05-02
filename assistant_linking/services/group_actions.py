from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from assistant_linking import models
from assistant_linking.services.grouping import rebuild_groups


@dataclass(frozen=True)
class GroupActionResult:
    group: object
    affected_count: int
    excluded_count: int = 0
    split_count: int = 0


@dataclass(frozen=True)
class RebuildGroupsResult:
    count: int

    @property
    def message(self):
        return f"Rebuilt {self.count} group memberships."


def rebuild_group_memberships(
    *,
    only_open=False,
    rebuilder=rebuild_groups,
):
    return RebuildGroupsResult(count=rebuilder(only_open=only_open))


def apply_group_action(
    *,
    group_id,
    action,
    item_ids,
    reason="",
    group_model=models.MatchGroup,
    item_model=models.MatchGroupItem,
    now=timezone.now,
):
    with transaction.atomic():
        group = get_object_or_404(
            group_model.objects.select_for_update(),
            pk=group_id,
        )
        items = group.items.select_for_update().filter(id__in=item_ids)
        if action == "exclude":
            excluded_count = items.update(
                role=item_model.ROLE_EXCLUDED,
                reasoning=reason,
            )
            return GroupActionResult(
                group=group,
                affected_count=excluded_count,
                excluded_count=excluded_count,
            )
        if action == "split":
            split_count = 0
            for item in items:
                new_group = group_model.objects.create(
                    group_key=(
                        f"{group.group_key}|split|"
                        f"{item.supplier_product_id}|{now().timestamp()}"
                    ),
                    normalized_brand=group.normalized_brand,
                    canonical_name=group.canonical_name,
                    concentration=group.concentration,
                    audience_hint=group.audience_hint,
                    size_ml=group.size_ml,
                    packaging=group.packaging,
                    variant_type=group.variant_type,
                    status=group_model.STATUS_OPEN,
                    confidence=max(group.confidence - 10, 0),
                )
                item.match_group = new_group
                item.role = item_model.ROLE_SPLIT
                item.reasoning = reason or "Split by operator"
                item.save()
                split_count += 1
            return GroupActionResult(
                group=group,
                affected_count=split_count,
                split_count=split_count,
            )
        return GroupActionResult(group=group, affected_count=0)
