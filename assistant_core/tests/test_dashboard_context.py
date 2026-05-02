from __future__ import annotations

from django.test import TestCase

from assistant_core.models import BrandWatchProfile, DetectedChange, GlobalRule
from assistant_core.services.dashboard import build_dashboard_context
from assistant_linking.models import (
    BrandAlias,
    LinkSuggestion,
    ManualLinkDecision,
    ParsedSupplierProduct,
)
from assistant_linking.services.garbage import GARBAGE_MODIFIER
from catalog.models import AIDraft, Brand, FactClaim, Perfume, Source
from prices.models import Supplier, SupplierProduct


class DashboardContextServiceTests(TestCase):
    def test_build_dashboard_context_counts_workflow_state(self):
        initial_cards = {
            title: count
            for title, _route, count in build_dashboard_context()["cards"]
        }
        supplier = Supplier.objects.create(name="Supplier A", code="supplier-a")
        brand = Brand.objects.create(name="Montale")
        perfume = Perfume.objects.create(brand=brand, name="Vanilla Extasy")
        source = Source.objects.create(perfume=perfume, url="https://example.com")
        supplier_product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="supplier-a:vanilla",
            name="Montale Vanilla Extasy 100ml",
        )
        complete_product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="supplier-a:complete",
            name="Montale Complete 100ml",
        )
        garbage_product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="supplier-a:garbage",
            name="Montale Garbage 100ml",
        )
        ParsedSupplierProduct.objects.create(
            supplier_product=supplier_product,
            raw_name=supplier_product.name,
            normalized_text="montale vanilla extasy 100ml",
            normalized_brand=brand,
            product_name_text="vanilla extasy",
            confidence=70,
        )
        ParsedSupplierProduct.objects.create(
            supplier_product=complete_product,
            raw_name=complete_product.name,
            normalized_text="montale complete 100ml",
            normalized_brand=brand,
            product_name_text="complete",
            concentration="Eau de Parfum",
            size_ml="100",
            confidence=95,
        )
        ParsedSupplierProduct.objects.create(
            supplier_product=garbage_product,
            raw_name=garbage_product.name,
            normalized_text="montale garbage 100ml",
            modifiers=[GARBAGE_MODIFIER],
            confidence=5,
        )
        BrandAlias.objects.create(
            brand=brand, alias_text="mntl", normalized_alias="mntl"
        )
        GlobalRule.objects.create(
            title="Rule",
            rule_kind="parser",
            scope_type="global",
            rule_text="Rule text",
        )
        BrandWatchProfile.objects.create(brand=brand, active=True)
        DetectedChange.objects.create(
            brand_profile=brand.watch_profile,
            change_type="new_perfume",
            status=DetectedChange.STATUS_PENDING,
        )
        FactClaim.objects.create(
            perfume=perfume,
            source=source,
            field_name="summary",
            value_json={"value": "A vanilla perfume"},
            claim_hash="summary-hash",
            status=FactClaim.STATUS_PENDING,
        )
        AIDraft.objects.create(perfume=perfume, status=AIDraft.STATUS_PENDING)
        decision = ManualLinkDecision.objects.create(
            supplier_product=supplier_product,
            perfume=perfume,
            decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
        )
        LinkSuggestion.objects.create(
            supplier_product=supplier_product,
            suggested_perfume=perfume,
            status=LinkSuggestion.STATUS_PENDING,
            confidence=90,
        )

        context = build_dashboard_context()

        cards = {title: count for title, _route, count in context["cards"]}
        self.assertEqual(cards["Normalisation issues"], 1)
        self.assertEqual(cards["Catalogue"], 1)
        self.assertEqual(cards["Linking Workbench"], 1)
        self.assertEqual(
            cards["Knowledge Base"],
            initial_cards["Knowledge Base"] + 2,
        )
        self.assertEqual(cards["Brand Managers"], 1)
        self.assertEqual(cards["Research Review"], 1)
        self.assertEqual(cards["AI Drafts"], 1)
        self.assertEqual(context["pending_approvals"], 2)
        self.assertEqual(context["low_confidence"], 1)
        self.assertEqual(context["normalization_issue_count"], 1)
        self.assertEqual(list(context["recent_decisions"]), [decision])
