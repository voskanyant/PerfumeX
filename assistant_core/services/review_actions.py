from __future__ import annotations

from django.utils import timezone

from assistant_core import models as assistant_models
from catalog.models import AIDraft, FactClaim


def is_valid_status(status: str, choices) -> bool:
    return status in dict(choices)


def update_detected_change_status(
    change,
    status: str,
    user,
    *,
    now_func=timezone.now,
) -> bool:
    if not is_valid_status(status, assistant_models.DetectedChange.STATUS_CHOICES):
        return False

    change.status = status
    change.resolved_by = user
    change.resolved_at = now_func()
    change.save(update_fields=["status", "resolved_by", "resolved_at"])
    return True


def update_fact_claim_status(
    claim,
    status: str,
    user,
    *,
    now_func=timezone.now,
) -> bool:
    if not is_valid_status(status, FactClaim.STATUS_CHOICES):
        return False

    claim.status = status
    claim.reviewed_by = user
    claim.reviewed_at = now_func()
    claim.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    return True


def update_ai_draft_status(
    draft,
    status: str,
    user,
    *,
    now_func=timezone.now,
) -> bool:
    if not is_valid_status(status, AIDraft.STATUS_CHOICES):
        return False

    draft.status = status
    if status == AIDraft.STATUS_APPROVED:
        draft.approved_by = user
        draft.approved_at = now_func()
    draft.save()
    return True
