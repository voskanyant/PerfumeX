from datetime import datetime
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from assistant_core.models import DetectedChange
from assistant_core.services.review_actions import (
    update_ai_draft_status,
    update_detected_change_status,
    update_fact_claim_status,
)
from catalog.models import AIDraft, FactClaim


class ReviewActionServiceTests(SimpleTestCase):
    def test_update_detected_change_status_sets_resolution_fields(self):
        now = timezone.make_aware(datetime(2026, 5, 1, 9, 0, 0))
        user = SimpleNamespace(id=7)
        change = SimpleNamespace(status="", resolved_by=None, resolved_at=None)
        change.save_calls = []
        change.save = lambda **kwargs: change.save_calls.append(kwargs)

        updated = update_detected_change_status(
            change,
            DetectedChange.STATUS_APPROVED,
            user,
            now_func=lambda: now,
        )

        self.assertTrue(updated)
        self.assertEqual(change.status, DetectedChange.STATUS_APPROVED)
        self.assertIs(change.resolved_by, user)
        self.assertEqual(change.resolved_at, now)
        self.assertEqual(
            change.save_calls,
            [{"update_fields": ["status", "resolved_by", "resolved_at"]}],
        )

    def test_update_fact_claim_status_sets_review_fields(self):
        now = timezone.make_aware(datetime(2026, 5, 1, 10, 0, 0))
        user = SimpleNamespace(id=9)
        claim = SimpleNamespace(status="", reviewed_by=None, reviewed_at=None)
        claim.save_calls = []
        claim.save = lambda **kwargs: claim.save_calls.append(kwargs)

        updated = update_fact_claim_status(
            claim,
            FactClaim.STATUS_REJECTED,
            user,
            now_func=lambda: now,
        )

        self.assertTrue(updated)
        self.assertEqual(claim.status, FactClaim.STATUS_REJECTED)
        self.assertIs(claim.reviewed_by, user)
        self.assertEqual(claim.reviewed_at, now)
        self.assertEqual(
            claim.save_calls,
            [{"update_fields": ["status", "reviewed_by", "reviewed_at"]}],
        )

    def test_update_ai_draft_status_approves_with_audit_fields(self):
        now = timezone.make_aware(datetime(2026, 5, 1, 11, 0, 0))
        user = SimpleNamespace(id=11)
        draft = SimpleNamespace(status="", approved_by=None, approved_at=None)
        draft.save_calls = []
        draft.save = lambda **kwargs: draft.save_calls.append(kwargs)

        updated = update_ai_draft_status(
            draft,
            AIDraft.STATUS_APPROVED,
            user,
            now_func=lambda: now,
        )

        self.assertTrue(updated)
        self.assertEqual(draft.status, AIDraft.STATUS_APPROVED)
        self.assertIs(draft.approved_by, user)
        self.assertEqual(draft.approved_at, now)
        self.assertEqual(draft.save_calls, [{}])

    def test_review_actions_ignore_invalid_status(self):
        item = SimpleNamespace(status="pending")
        item.save_calls = []
        item.save = lambda **kwargs: item.save_calls.append(kwargs)

        self.assertFalse(update_fact_claim_status(item, "bad", object()))
        self.assertEqual(item.status, "pending")
        self.assertEqual(item.save_calls, [])
