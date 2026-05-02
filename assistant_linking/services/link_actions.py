from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from assistant_linking import models
from assistant_linking.services.normalization_detail import record_manual_link_decision
from prices.models import SupplierProduct


BULK_LINK_PRODUCT_CAP = 200
BULK_LINK_ASYNC_PRODUCT_THRESHOLD = 20
UNDO_WINDOW_SECONDS = 30


@dataclass(frozen=True)
class BulkLinkSelection:
    source: object
    perfume_id: object
    variant_id: object
    product_ids: list[int]
    allow_overwrite: bool
    apply_to_similar: bool
    error_message: str = ""
    error_status: int | None = None

    @property
    def has_error(self):
        return bool(self.error_message)


def _posted_values(post_data, key):
    if hasattr(post_data, "getlist"):
        return post_data.getlist(key)
    value = post_data.get(key, [])
    return value if isinstance(value, (list, tuple)) else [value]


def _numeric_ids(values):
    return [int(value) for value in values if str(value).isdigit()]


def _unique_ids(values):
    return list(dict.fromkeys(values))


def build_bulk_link_selection(
    *,
    supplier_product_id,
    post_data,
    supplier_product_model=SupplierProduct,
    group_model=models.MatchGroup,
    product_cap=BULK_LINK_PRODUCT_CAP,
):
    source = get_object_or_404(
        supplier_product_model.objects.select_related(
            "catalog_perfume",
            "catalog_variant",
        ),
        pk=supplier_product_id,
    )
    perfume_id = post_data.get("perfume_id") or source.catalog_perfume_id
    variant_id = post_data.get("variant_id") or source.catalog_variant_id
    allow_overwrite = post_data.get("confirm_overwrite") == "1"
    apply_to_similar = post_data.get("apply_to_similar") == "1"
    if not perfume_id:
        return BulkLinkSelection(
            source=source,
            perfume_id=perfume_id,
            variant_id=variant_id,
            product_ids=[],
            allow_overwrite=allow_overwrite,
            apply_to_similar=apply_to_similar,
            error_message="Choose or approve a catalogue perfume before linking rows.",
        )

    if apply_to_similar:
        group = group_model.objects.filter(items__supplier_product=source).first()
        queryset = (
            supplier_product_model.objects.filter(
                assistant_group_items__match_group=group
            )
            if group
            else supplier_product_model.objects.filter(pk=source.pk)
        )
        product_ids = list(queryset.order_by("id").values_list("id", flat=True))
        if post_data.get("confirm_apply_to_similar") != "1":
            return BulkLinkSelection(
                source=source,
                perfume_id=perfume_id,
                variant_id=variant_id,
                product_ids=product_ids,
                allow_overwrite=allow_overwrite,
                apply_to_similar=apply_to_similar,
                error_message=(
                    "Confirm apply_to_similar before linking "
                    f"{len(product_ids)} matched products."
                ),
                error_status=409,
            )
    else:
        product_ids = _numeric_ids(
            _posted_values(post_data, "supplier_product_ids")
        ) or [source.id]

    product_ids = _unique_ids(product_ids)
    if len(product_ids) > product_cap:
        return BulkLinkSelection(
            source=source,
            perfume_id=perfume_id,
            variant_id=variant_id,
            product_ids=product_ids,
            allow_overwrite=allow_overwrite,
            apply_to_similar=apply_to_similar,
            error_message=(
                f"Bulk link matched {len(product_ids)} products; "
                f"narrow scope to {product_cap} or fewer."
            ),
            error_status=409,
        )

    return BulkLinkSelection(
        source=source,
        perfume_id=perfume_id,
        variant_id=variant_id,
        product_ids=product_ids,
        allow_overwrite=allow_overwrite,
        apply_to_similar=apply_to_similar,
    )


def build_bulk_link_status_payload(action, *, undo_url):
    payload = action.payload_json or {}
    matched = int(payload.get("matched") or 0)
    linked = int(payload.get("linked") or 0)
    skipped = int(payload.get("skipped") or 0)
    processed = linked + skipped
    return {
        "job_id": action.id,
        "status": payload.get("status", "COMPLETE"),
        "matched": matched,
        "linked": linked,
        "skipped": skipped,
        "processed": processed,
        "percent": 100 if matched else 0,
        "undo_url": undo_url,
    }


def should_return_bulk_link_async_response(
    *,
    product_ids,
    headers,
    product_threshold=BULK_LINK_ASYNC_PRODUCT_THRESHOLD,
):
    return len(product_ids) > product_threshold or is_ajax_request(headers)


def is_ajax_request(headers):
    return headers.get("x-requested-with") == "XMLHttpRequest"


def build_bulk_link_accepted_payload(action, *, status_url, undo_url):
    return {
        "job_id": action.id,
        "status_url": status_url,
        "undo_url": undo_url,
    }


def bulk_link_success_message(action):
    payload = action.payload_json or {}
    return f"Linked {payload.get('linked', 0)} products."


def build_undo_link_payload(restored):
    return {"restored": restored, "status": "UNDONE"}


def undo_link_success_message(restored):
    return f"Undid {restored} linked product(s)."


def prune_link_actions(user, *, link_action_model=models.LinkAction):
    stale_ids = list(
        link_action_model.objects.filter(user=user)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)[50:]
    )
    if stale_ids:
        link_action_model.objects.filter(id__in=stale_ids).delete()


def latest_undoable_action(
    user,
    *,
    link_action_model=models.LinkAction,
    undo_window_seconds=UNDO_WINDOW_SECONDS,
    now=timezone.now,
):
    cutoff = now() - timedelta(seconds=undo_window_seconds)
    return (
        link_action_model.objects.filter(
            user=user,
            action_type=link_action_model.ACTION_BULK_LINK,
            created_at__gte=cutoff,
        )
        .exclude(payload_json__status="UNDONE")
        .order_by("-created_at", "-id")
        .first()
    )


def bulk_link_products(
    *,
    user,
    product_ids,
    perfume_id,
    variant_id,
    allow_overwrite,
    apply_to_similar,
    reason,
    supplier_product_model=SupplierProduct,
    link_action_model=models.LinkAction,
    decision_recorder=record_manual_link_decision,
):
    payload_items = []
    linked = 0
    skipped = 0
    with transaction.atomic():
        products = list(
            supplier_product_model.objects.select_for_update()
            .filter(id__in=product_ids)
            .order_by("id")
        )
        for product in products:
            had_link = bool(product.catalog_perfume_id or product.catalog_variant_id)
            previous = {
                "product_id": product.id,
                "catalog_perfume_id": product.catalog_perfume_id,
                "catalog_variant_id": product.catalog_variant_id,
            }
            if had_link and not allow_overwrite:
                skipped += 1
                payload_items.append({**previous, "linked": False, "skipped": True})
                continue
            product.catalog_perfume_id = perfume_id or None
            product.catalog_variant_id = variant_id or None
            product.save(
                update_fields=["catalog_perfume", "catalog_variant", "updated_at"]
            )
            decision_recorder(
                supplier_product=product,
                perfume_id=perfume_id or None,
                variant_id=variant_id or None,
                decision_type=(
                    models.ManualLinkDecision.DECISION_APPROVE_VARIANT
                    if variant_id
                    else models.ManualLinkDecision.DECISION_APPROVE_PERFUME
                ),
                reason=reason,
                apply_to_similar=apply_to_similar or len(product_ids) > 1,
                created_by=user,
                allow_overwrite=allow_overwrite and had_link,
            )
            linked += 1
            payload_items.append(
                {
                    **previous,
                    "linked": True,
                    "skipped": False,
                    "new_catalog_perfume_id": perfume_id or None,
                    "new_catalog_variant_id": variant_id or None,
                }
            )
    action = link_action_model.objects.create(
        user=user,
        action_type=link_action_model.ACTION_BULK_LINK,
        payload_json={
            "status": "COMPLETE",
            "matched": len(product_ids),
            "linked": linked,
            "skipped": skipped,
            "items": payload_items,
        },
    )
    prune_link_actions(user, link_action_model=link_action_model)
    return action


def undo_link_action(
    action,
    user,
    *,
    supplier_product_model=SupplierProduct,
    link_action_model=models.LinkAction,
    now=timezone.now,
):
    payload = action.payload_json or {}
    items = payload.get("items") or []
    restored = 0
    with transaction.atomic():
        for item in items:
            if not item.get("linked"):
                continue
            product = supplier_product_model.objects.select_for_update().get(
                pk=item["product_id"]
            )
            product.catalog_perfume_id = item.get("catalog_perfume_id")
            product.catalog_variant_id = item.get("catalog_variant_id")
            product.save(
                update_fields=["catalog_perfume", "catalog_variant", "updated_at"]
            )
            restored += 1
        link_action_model.objects.create(
            user=user,
            action_type=link_action_model.ACTION_UNDO_BULK_LINK,
            payload_json={"undone_action_id": action.id, "restored": restored},
        )
        action.payload_json = {
            **payload,
            "status": "UNDONE",
            "undone_at": now().isoformat(),
        }
        action.save(update_fields=["payload_json"])
    prune_link_actions(user, link_action_model=link_action_model)
    return restored


def get_undoable_bulk_link_action(
    *,
    action_id,
    user,
    link_action_model=models.LinkAction,
    undo_window_seconds=UNDO_WINDOW_SECONDS,
    now=timezone.now,
):
    cutoff = now() - timedelta(seconds=undo_window_seconds)
    action = get_object_or_404(
        link_action_model,
        pk=action_id,
        user=user,
        action_type=link_action_model.ACTION_BULK_LINK,
        created_at__gte=cutoff,
    )
    if (action.payload_json or {}).get("status") == "UNDONE":
        raise Http404("Action already undone.")
    return action
