from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_core.models import BrandWatchProfile, DetectedChange, GlobalRule, KnowledgeNote, SupplierRule
from assistant_core.services.context_builder import build_assistant_context
from assistant_core.services.mock_brand_research import run_mock_brand_watch
from assistant_linking.models import BrandAlias, ConcentrationAlias, ManualLinkDecision, ProductAlias
from catalog.models import Brand
from prices.models import Supplier, SupplierProduct


User = get_user_model()


class KnowledgeResearchTests(TestCase):
    def test_knowledge_page_shows_taught_aliases_and_decisions(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)
        supplier = Supplier.objects.create(name="Supplier", code="sup")
        product = SupplierProduct.objects.create(supplier=supplier, identity_key="1", name="montale vanilla extasy")
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(brand=brand, supplier=supplier, alias_text="mntl", normalized_alias="mntl")
        ProductAlias.objects.create(
            brand=brand,
            supplier=supplier,
            alias_text="vanilla extasy",
            canonical_text="Vanilla Extasy",
            excluded_terms="tester",
        )
        ManualLinkDecision.objects.create(
            supplier_product=product,
            decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
            reason="manual match",
            created_by=user,
        )

        response = self.client.get(reverse("assistant_core:knowledge"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Brand aliases")
        self.assertContains(response, "Search")
        self.assertContains(response, "mntl")

        response = self.client.get(reverse("assistant_core:knowledge"), {"section": "product_aliases", "q": "tester"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Product aliases")
        self.assertContains(response, "vanilla extasy")
        self.assertContains(response, "tester")

        response = self.client.get(reverse("assistant_core:knowledge"), {"section": "decisions", "q": "manual match"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manual decisions")
        self.assertContains(response, "manual match")

    def test_knowledge_page_paginates_large_alias_sets(self):
        user = User.objects.create_user(username="staff-page", password="pass", is_staff=True)
        self.client.force_login(user)
        brand = Brand.objects.create(name="Montale")
        for index in range(55):
            BrandAlias.objects.create(
                brand=brand,
                alias_text=f"montale alias {index:02d}",
                normalized_alias=f"montale alias {index:02d}",
            )

        response = self.client.get(reverse("assistant_core:knowledge"), {"section": "brand_aliases"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Showing 1-50 of 55 entries")
        self.assertContains(response, "Next")

    def test_aliases_page_supports_sections_search_and_concentration_entries(self):
        user = User.objects.create_user(username="staff2", password="pass", is_staff=True)
        self.client.force_login(user)
        supplier = Supplier.objects.create(name="Supplier", code="sup")
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(brand=brand, supplier=supplier, alias_text="mntl", normalized_alias="mntl")
        ProductAlias.objects.create(
            brand=brand,
            supplier=supplier,
            alias_text="vanilla extasy",
            canonical_text="Vanilla Extasy",
            excluded_terms="tester",
        )
        ConcentrationAlias.objects.create(
            concentration="Eau de Parfum",
            alias_text="парфюмированная вода",
            normalized_alias="парфюмированная вода",
        )

        response = self.client.get(reverse("assistant_core:aliases"), {"section": "concentrations", "q": "парфюмированная"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Concentration aliases")
        self.assertContains(response, "парфюмированная вода")
        self.assertContains(response, "Eau de Parfum")

    def test_context_builder_includes_only_approved_active_rules(self):
        supplier = Supplier.objects.create(name="Supplier", code="sup")
        product = SupplierProduct.objects.create(supplier=supplier, identity_key="1", name="Name")
        GlobalRule.objects.create(title="yes", rule_kind="linking", scope_type="global", rule_text="use", priority=0, approved=True, active=True)
        GlobalRule.objects.create(title="no", rule_kind="linking", scope_type="global", rule_text="ignore", approved=False, active=True)
        SupplierRule.objects.create(supplier=supplier, title="supplier", rule_kind="linking", rule_text="use", approved=True, active=True)
        KnowledgeNote.objects.create(category="brand", title="note", content="visible", supplier=supplier, active=True)

        context = build_assistant_context(supplier_product_id=product.id)

        global_rule_titles = [rule["title"] for rule in context["global_rules"]]
        self.assertIn("yes", global_rule_titles)
        self.assertNotIn("no", global_rule_titles)
        self.assertEqual([rule["title"] for rule in context["supplier_rules"]], ["supplier"])
        self.assertEqual(len(context["knowledge_notes"]), 1)

    def test_mock_brand_watch_creates_review_records_without_mutating_brand(self):
        brand = Brand.objects.create(name="Fixture Brand", official_url="")
        profile = BrandWatchProfile.objects.create(brand=brand, official_url="https://example.com")

        job = run_mock_brand_watch(profile.id)
        brand.refresh_from_db()

        self.assertEqual(job.status, "finished")
        self.assertEqual(DetectedChange.objects.filter(brand_profile=profile).count(), 1)
        self.assertEqual(brand.official_url, "")
