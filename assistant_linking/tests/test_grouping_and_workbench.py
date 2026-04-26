from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from assistant_linking.models import (
    BrandAlias,
    LinkAction,
    LinkSuggestion,
    ManualLinkDecision,
    ManualLinkDecisionAudit,
    MatchGroup,
    MatchGroupItem,
    ProductAlias,
)
from assistant_linking.services.grouping import rebuild_groups
from assistant_linking.services.normalizer import save_parse
from catalog.models import Brand, Perfume
from prices.models import Supplier, SupplierProduct


User = get_user_model()


class GroupingWorkbenchTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.brand = Brand.objects.create(name="Brand")
        self.perfume = Perfume.objects.create(brand=self.brand, name="Hero", concentration="Eau de Parfum")
        self.s1 = Supplier.objects.create(name="S1", code="s1")
        self.s2 = Supplier.objects.create(name="S2", code="s2")
        self.p1 = SupplierProduct.objects.create(supplier=self.s1, identity_key="1", name="Brand Hero EDP 100ml")
        self.p2 = SupplierProduct.objects.create(supplier=self.s2, identity_key="2", name="Brand Hero EDP 100ml", is_active=False)

    def test_grouping_includes_inactive_rows(self):
        save_parse(self.p1)
        save_parse(self.p2)
        rebuild_groups()

        self.assertEqual(MatchGroupItem.objects.count(), 2)
        self.assertTrue(MatchGroupItem.objects.filter(supplier_product=self.p2).exists())

    def test_parse_teaching_invalid_form_preserves_typed_values(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("assistant_linking:normalization_teach", args=[self.p1.id]),
            {
                "supplier_brand_text": "Typed Supplier Brand",
                "brand_name": "",
                "supplier_product_text": "Typed Supplier Scent",
                "product_name": "Typed Correct Scent",
                "product_excluded_terms": "typed blocker",
                "supplier_concentration_text": "typed edp",
                "concentration": "Eau de Parfum",
                "supplier_size_text": "typed 100ml",
                "size_ml": "100",
                "supplier_audience_text": "typed unisex",
                "audience": "unisex",
                "supplier_type_text": "typed standard",
                "variant_type": "standard",
                "supplier_packaging_text": "typed tester",
                "packaging": "tester",
                "alias_scope": "global",
                "lock_parse": "on",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('id="teach-form"', body)
        self.assertIn("#teach-form", body)
        self.assertIn("Typed Supplier Brand", body)
        self.assertIn("Typed Correct Scent", body)
        self.assertIn("typed blocker", body)
        self.assertIn("id_brand_name_errors", body)
        self.assertIn('aria-describedby="id_brand_name_errors"', body)

    def test_bulk_link_does_not_overwrite_without_confirmation(self):
        self.p2.catalog_perfume = self.perfume
        self.p2.save()
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("assistant_linking:bulk_link", args=[self.p1.id]),
            {"supplier_product_ids": [self.p2.id], "perfume_id": self.perfume.id, "reason": "test"},
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ManualLinkDecision.objects.count(), 0)

    def test_bulk_link_overwrite_records_manual_decision_audit(self):
        original = ManualLinkDecision.objects.create(
            supplier_product=self.p2,
            perfume=self.perfume,
            decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
            reason="old",
            created_by=self.staff,
        )
        self.p2.catalog_perfume = self.perfume
        self.p2.save(update_fields=["catalog_perfume", "updated_at"])
        replacement = Perfume.objects.create(
            brand=self.brand,
            name="Replacement",
            concentration="Eau de Parfum",
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("assistant_linking:bulk_link", args=[self.p1.id]),
            {
                "supplier_product_ids": [self.p2.id],
                "perfume_id": replacement.id,
                "confirm_overwrite": "1",
                "reason": "replace",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ManualLinkDecisionAudit.objects.count(), 1)
        audit = ManualLinkDecisionAudit.objects.get()
        self.assertEqual(audit.previous_pk, original.pk)
        self.assertEqual(audit.previous_decision_json["reason"], "old")
        self.assertEqual(audit.replaced_by.perfume_id, replacement.id)

    def test_brand_alias_rejects_redos_shaped_regex(self):
        alias = BrandAlias(
            brand=self.brand,
            alias_text="dangerous",
            normalized_alias=r"(.+)+",
            is_regex=True,
        )

        with self.assertRaises(ValidationError) as ctx:
            alias.full_clean()

        self.assertIn("catastrophic-backtracking shape", str(ctx.exception))

    def test_bulk_link_apply_to_similar_over_cap_requires_narrow_scope(self):
        group = MatchGroup.objects.create(
            group_key="brand|hero|edp|100",
            normalized_brand=self.brand,
            canonical_name="Hero",
            concentration="Eau de Parfum",
            candidate_perfume=self.perfume,
        )
        MatchGroupItem.objects.create(match_group=group, supplier_product=self.p1)
        products = [
            SupplierProduct(supplier=self.s1, identity_key=f"bulk-{index}", name=f"Brand Hero EDP {index}")
            for index in range(201)
        ]
        SupplierProduct.objects.bulk_create(products)
        for product_id in SupplierProduct.objects.filter(identity_key__startswith="bulk-").values_list("id", flat=True):
            MatchGroupItem.objects.create(match_group=group, supplier_product_id=product_id)
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("assistant_linking:bulk_link", args=[self.p1.id]),
            {
                "perfume_id": self.perfume.id,
                "apply_to_similar": "1",
                "confirm_apply_to_similar": "1",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("narrow scope", response.content.decode())

    def test_queue_view_renders_shortcut_help(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("assistant_linking:group_queue"), secure=True)

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("assistant_linking/js/queue-keys.js", body)
        self.assertIn("data-shortcut-dialog", body)
        self.assertIn("Queue shortcuts", body)

    def test_undo_within_window_reverses_link(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("assistant_linking:bulk_link", args=[self.p1.id]),
            {"supplier_product_ids": [self.p1.id], "perfume_id": self.perfume.id, "reason": "undo me"},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.catalog_perfume_id, self.perfume.id)
        action = LinkAction.objects.get(action_type=LinkAction.ACTION_BULK_LINK)

        response = self.client.post(reverse("assistant_linking:undo_link_action", args=[action.id]), secure=True)

        self.assertEqual(response.status_code, 302)
        self.p1.refresh_from_db()
        self.assertIsNone(self.p1.catalog_perfume_id)
        self.assertTrue(LinkAction.objects.filter(action_type=LinkAction.ACTION_UNDO_BULK_LINK).exists())

    def test_undo_outside_window_returns_404(self):
        self.client.force_login(self.staff)
        action = LinkAction.objects.create(
            user=self.staff,
            action_type=LinkAction.ACTION_BULK_LINK,
            payload_json={"status": "COMPLETE", "items": []},
        )
        LinkAction.objects.filter(pk=action.pk).update(created_at=timezone.now() - timedelta(seconds=31))

        response = self.client.post(reverse("assistant_linking:undo_link_action", args=[action.id]), secure=True)

        self.assertEqual(response.status_code, 404)


class ConcurrentSuggestionAcceptanceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        ProductAlias.objects.all().delete()
        self.staff1 = User.objects.create_user(username="staff1", password="pass", is_staff=True)
        self.staff2 = User.objects.create_user(username="staff2", password="pass", is_staff=True)
        self.brand = Brand.objects.create(name="Race Brand")
        self.perfume = Perfume.objects.create(
            brand=self.brand,
            name="Race Hero",
            concentration="Eau de Parfum",
        )
        self.supplier = Supplier.objects.create(name="Race Supplier", code="race")
        self.product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="race-1",
            name="Race Brand Race Hero EDP 100ml",
        )
        save_parse(self.product)
        self.suggestion = LinkSuggestion.objects.create(
            supplier_product=self.product,
            suggested_perfume=self.perfume,
            confidence=95,
            status=LinkSuggestion.STATUS_PENDING,
        )

    def test_concurrent_suggestion_acceptance(self):
        first = Client()
        second = Client()
        first.force_login(self.staff1)
        second.force_login(self.staff2)
        url = reverse("assistant_linking:normalization_accept_candidate", args=[self.product.id])
        payload = {"perfume_id": str(self.perfume.id)}

        response1 = first.post(url, payload, secure=True)
        response2 = second.post(url, payload, secure=True)

        self.assertEqual(response1.status_code, 302)
        self.assertEqual(response2.status_code, 302)
        self.suggestion.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.suggestion.status, LinkSuggestion.STATUS_APPROVED)
        self.assertEqual(self.product.catalog_perfume_id, self.perfume.id)
        self.assertEqual(ManualLinkDecision.objects.count(), 1)
        warning_messages = [str(message) for message in get_messages(response2.wsgi_request)]
        self.assertIn("This suggestion was already handled by another user.", warning_messages)
