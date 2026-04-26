from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from assistant_core.models import GlobalRule
from assistant_linking.models import BrandAlias
from assistant_linking.services.normalizer import parse_supplier_product
from catalog.models import Brand
from prices.models import Supplier, SupplierProduct


User = get_user_model()


class AssistantDashboardTests(TestCase):
    def test_staff_user_can_open_dashboard(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)

        response = self.client.get(reverse("assistant_core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assistant control room")

    def test_non_staff_user_redirects_from_admin_assistant(self):
        user = User.objects.create_user(username="viewer", password="pass", is_staff=False)
        self.client.force_login(user)

        response = self.client.get(reverse("assistant_core:dashboard"))

        self.assertEqual(response.status_code, 302)

    def test_knowledge_card_counts_taught_aliases(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(brand=brand, alias_text="mntl", normalized_alias="mntl")

        response = self.client.get(reverse("assistant_core:dashboard"))

        self.assertEqual(response.status_code, 200)
        cards = {title: count for title, _route, count in response.context["cards"]}
        self.assertGreaterEqual(cards["Knowledge Base"], 1)

    def test_knowledge_parser_terms_section_renders(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)
        GlobalRule.objects.create(
            title="Mini term: miniature",
            rule_kind="parser_mini_term",
            scope_type="global",
            rule_text="miniature",
            active=True,
            approved=True,
        )

        response = self.client.get(reverse("assistant_core:knowledge"), {"section": "parser_terms", "q": "miniature"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parser terms")
        self.assertContains(response, "parser_mini_term")
        self.assertContains(response, "miniature")
        self.assertContains(response, "Audience alias")
        self.assertContains(response, "fem =&gt; Woman | women")

    def test_parser_term_create_updates_parser_behavior(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)
        supplier = Supplier.objects.create(name="Supplier", code="sup")

        response = self.client.post(
            reverse("assistant_core:parser_term_create"),
            {"rule_kind": "parser_refill_term", "terms": "custom-refill"},
        )

        self.assertEqual(response.status_code, 302)
        product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="kb-refill",
            name="Some Brand custom-refill 100ml",
        )
        parsed = parse_supplier_product(product)
        self.assertIn("refill", parsed.modifiers)

    def test_invalid_parser_audience_term_is_not_saved(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)

        response = self.client.post(
            reverse("assistant_core:parser_term_create"),
            {"rule_kind": "parser_audience_term", "terms": "bad audience row"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(GlobalRule.objects.filter(rule_kind="parser_audience_term", rule_text="bad audience row").exists())

    def test_parser_audience_term_create_updates_parser_behavior(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)
        supplier = Supplier.objects.create(name="Supplier", code="sup")

        response = self.client.post(
            reverse("assistant_core:parser_term_create"),
            {"rule_kind": "parser_audience_term", "terms": "ladies => Woman | women"},
        )

        self.assertEqual(response.status_code, 302)
        product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="kb-audience",
            name="Some Brand Scent ladies 100ml",
        )
        parsed = parse_supplier_product(product)
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(parsed.product_name_text, "some brand scent")

    def test_garbage_keyword_create_clears_garbage_cache(self):
        cache.clear()
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)
        supplier = Supplier.objects.create(name="Supplier", code="sup")
        GlobalRule.objects.create(
            title="Garbage keyword: old-trash",
            rule_kind="garbage_keyword",
            scope_type="global",
            rule_text="old-trash",
            active=True,
            approved=True,
        )
        old_product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="old-trash",
            name="Some Brand old-trash 100ml",
        )
        self.assertEqual(parse_supplier_product(old_product).modifiers, ["garbage"])

        response = self.client.post(reverse("assistant_core:garbage_keyword_create"), {"keywords": "new-trash"})

        self.assertEqual(response.status_code, 302)
        new_product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="new-trash",
            name="Some Brand new-trash 100ml",
        )
        self.assertEqual(parse_supplier_product(new_product).modifiers, ["garbage"])
