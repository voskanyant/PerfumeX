from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import BrandAlias, ParsedSupplierProduct, ProductAlias
from assistant_linking.services.catalog_matcher import rule_impact
from assistant_linking.services.normalizer import save_parse
from catalog.models import Brand, Perfume, PerfumeVariant
from prices.models import Supplier, SupplierProduct, UserPreference


User = get_user_model()


class TeachParseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name="Supplier", code="sup")
        self.product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="montale-1",
            name="montale vanilla extasy edp100ml",
        )
        self.intense_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dg-1",
            name="dolce gabanna light blue eau intense eau de parfum 100ml",
        )
        save_parse(self.product)

    def test_staff_can_teach_parse_inline(self):
        response = self.client.post(
            reverse("assistant_linking:normalization_teach", args=[self.product.id]),
            {
                "supplier_brand_text": "montale",
                "brand_name": "Montale",
                "supplier_product_text": "vanilla extasy",
                "product_name": "Vanilla Extasy",
                "product_excluded_terms": "",
                "supplier_concentration_text": "edp",
                "concentration": "Eau de Parfum",
                "supplier_size_text": "100ml",
                "size_ml": "100",
                "supplier_audience_text": "",
                "audience": "",
                "supplier_type_text": "",
                "variant_type": "standard",
                "supplier_packaging_text": "",
                "packaging": "",
                "alias_scope": "global",
                "lock_parse": "on",
                "reparse_similar": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        brand = Brand.objects.get(name="Montale")
        parsed = ParsedSupplierProduct.objects.get(supplier_product=self.product)
        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Vanilla Extasy")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, 100)
        self.assertEqual(parsed.raw_size_text, "100ml")
        self.assertEqual(parsed.variant_type, "standard")
        self.assertTrue(parsed.locked_by_human)
        self.assertTrue(BrandAlias.objects.filter(brand=brand, alias_text="montale", supplier__isnull=True).exists())
        self.assertTrue(ProductAlias.objects.filter(brand=brand, alias_text="vanilla extasy", canonical_text="Vanilla Extasy").exists())

    def test_teaching_alias_can_block_identity_modifiers(self):
        response = self.client.post(
            reverse("assistant_linking:normalization_teach", args=[self.product.id]),
            {
                "supplier_brand_text": "dolce gabanna",
                "brand_name": "Dolce & Gabbana",
                "supplier_product_text": "light blue",
                "product_name": "Light Blue",
                "product_excluded_terms": "intense, love in capri",
                "supplier_concentration_text": "eau de parfum",
                "concentration": "Eau de Parfum",
                "supplier_size_text": "100ml",
                "size_ml": "100",
                "supplier_audience_text": "",
                "audience": "",
                "supplier_type_text": "",
                "variant_type": "standard",
                "supplier_packaging_text": "",
                "packaging": "",
                "alias_scope": "global",
                "lock_parse": "on",
                "reparse_similar": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        alias = ProductAlias.objects.get(alias_text="light blue", canonical_text="Light Blue")
        self.assertEqual(alias.excluded_terms, "intense, love in capri")

        parsed = save_parse(self.intense_product, force=True)
        self.assertNotEqual(parsed.product_name_text, "Light Blue")
        self.assertIn("intense", parsed.product_name_text)

    def test_teaching_page_preserves_existing_manual_blockers(self):
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(brand=brand, alias_text="montale", normalized_alias="montale")
        ProductAlias.objects.create(
            brand=brand,
            alias_text="vanilla extasy",
            canonical_text="Vanilla Extasy",
            excluded_terms="intense, tester",
            active=True,
        )
        save_parse(self.product, force=True)

        response = self.client.get(reverse("assistant_linking:normalization_detail", args=[self.product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "intense, tester")

    def test_blocked_modifier_can_be_taught_as_separate_product(self):
        brand = Brand.objects.create(name="Dolce & Gabbana")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="dolce gabanna",
            normalized_alias="dolce gabanna",
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="light blue",
            canonical_text="Light Blue",
            concentration="Eau de Parfum",
            excluded_terms="intense",
            priority=50,
            active=True,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="light blue eau intense",
            canonical_text="Light Blue Eau Intense",
            concentration="Eau de Parfum",
            priority=40,
            active=True,
        )

        parsed = save_parse(self.intense_product, force=True)

        self.assertEqual(parsed.product_name_text, "Light Blue Eau Intense")

    def test_product_aliases_are_limited_to_detected_brand(self):
        dolce = Brand.objects.create(name="Dolce & Gabbana")
        montale = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(brand=dolce, alias_text="dolce gabanna", normalized_alias="dolce gabanna")
        BrandAlias.objects.create(brand=montale, alias_text="montale", normalized_alias="montale")
        ProductAlias.objects.create(
            brand=dolce,
            alias_text="light blue",
            canonical_text="Dolce Classic Light Blue",
            priority=50,
            active=True,
        )
        montale_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="montale-light-blue",
            name="montale light blue eau de parfum 100ml",
        )

        parsed = save_parse(montale_product, force=True)

        self.assertEqual(parsed.normalized_brand, montale)
        self.assertNotEqual(parsed.product_name_text, "Dolce Classic Light Blue")

    def test_staff_can_accept_catalog_candidate_from_normalization(self):
        brand = Brand.objects.create(name="Montale")
        perfume = Perfume.objects.create(brand=brand, name="Vanilla Extasy", concentration="Eau de Parfum")
        variant = PerfumeVariant.objects.create(perfume=perfume, size_ml="100", variant_type="standard")

        response = self.client.post(
            reverse("assistant_linking:normalization_accept_candidate", args=[self.product.id]),
            {"perfume_id": perfume.id, "variant_id": variant.id},
        )

        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        parsed = ParsedSupplierProduct.objects.get(supplier_product=self.product)
        self.assertEqual(self.product.catalog_perfume, perfume)
        self.assertEqual(self.product.catalog_variant, variant)
        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Vanilla Extasy")
        self.assertTrue(parsed.locked_by_human)

    def test_missing_brand_list_refreshes_stale_parse_before_render(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="100bon-1",
            name="100 Bon Ambre Sensuel 50 ml edt",
        )
        stale_parse = save_parse(product)
        self.assertIsNone(stale_parse.normalized_brand)

        brand = Brand.objects.create(name="100 Bon")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="100 bon",
            normalized_alias="100 bon",
        )

        response = self.client.get(reverse("assistant_linking:normalization_missing_brand"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["parses"]), [])
        stale_parse.refresh_from_db()
        self.assertEqual(stale_parse.normalized_brand, brand)

    def test_parsed_products_page_supports_search(self):
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="montale",
            normalized_alias="montale",
        )
        matching = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="parsed-search-1",
            name="Montale Arabians Tonka EDP 100ml",
        )
        other = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="parsed-search-2",
            name="Attar Collection Hayati 100ml",
        )
        save_parse(matching)
        save_parse(other)

        response = self.client.get(
            reverse("assistant_linking:normalization_parsed"),
            {"q": "Arabians"},
        )

        self.assertEqual(response.status_code, 200)
        parses = list(response.context["parses"])
        self.assertEqual(len(parses), 1)
        self.assertEqual(parses[0].supplier_product_id, matching.id)

    def test_catalog_variant_label_hides_default_type_and_packaging(self):
        brand = Brand.objects.create(name="V Canto")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Ricina",
            concentration="Extrait de Parfum",
        )
        variant = PerfumeVariant.objects.create(
            perfume=perfume,
            size_ml="100",
            variant_type="standard",
            packaging="standard",
            is_tester=False,
        )
        tester_variant = PerfumeVariant.objects.create(
            perfume=perfume,
            size_ml="100",
            variant_type="standard",
            packaging="standard",
            is_tester=True,
        )

        self.assertEqual(str(variant), "V Canto / Ricina / 100ml")
        self.assertEqual(str(tester_variant), "V Canto / Ricina / 100ml / tester")

    def test_teaching_reparses_only_selected_preview_rows(self):
        target_brand = Brand.objects.create(name="Philly & Phill")
        similar_one = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="philly-1",
            name="Philly & Phill Romeo on the Rocks edp 100ml",
        )
        similar_two = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="philly-2",
            name="Philly Phill Romeo on the Rocks edp 30ml",
        )
        parsed_one = save_parse(similar_one)
        parsed_two = save_parse(similar_two)
        self.assertIsNone(parsed_one.normalized_brand)
        self.assertIsNone(parsed_two.normalized_brand)

        response = self.client.post(
            reverse("assistant_linking:normalization_teach", args=[self.product.id]),
            {
                "supplier_brand_text": "philly phill",
                "brand_name": "Philly & Phill",
                "supplier_product_text": "romeo on the rocks",
                "product_name": "Romeo on the Rocks",
                "product_excluded_terms": "",
                "supplier_concentration_text": "edp",
                "concentration": "Eau de Parfum",
                "supplier_size_text": "100ml",
                "size_ml": "100",
                "supplier_audience_text": "",
                "audience": "",
                "supplier_type_text": "",
                "variant_type": "standard",
                "supplier_packaging_text": "",
                "packaging": "",
                "alias_scope": "global",
                "lock_parse": "on",
                "reparse_similar": "on",
                "selected_similar_ids": [str(similar_one.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        parsed_one.refresh_from_db()
        parsed_two.refresh_from_db()
        self.assertEqual(parsed_one.normalized_brand, target_brand)
        self.assertIsNone(parsed_two.normalized_brand)

    def test_rule_impact_returns_all_matching_rows(self):
        for index in range(12):
            SupplierProduct.objects.create(
                supplier=self.supplier,
                identity_key=f"romeo-{index}",
                name=f"Philly Phill Romeo on the Rocks edp {index}0ml",
            )

        impact = rule_impact(
            self.product,
            brand_alias_text="philly phill",
            product_alias_text="romeo on the rocks",
        )

        self.assertEqual(impact["count"], 12)
        self.assertEqual(len(impact["examples"]), 12)

    def test_hidden_product_keywords_filter_parsed_products_page(self):
        hidden = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="hidden-1",
            name="Montale Hidden Tester 100ml",
        )
        visible = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="visible-1",
            name="Montale Arabians Tonka 100ml",
        )
        save_parse(hidden)
        save_parse(visible)
        prefs = UserPreference.get_for_user(self.user)
        prefs.supplier_exclude_terms = "tester"
        prefs.save(update_fields=["supplier_exclude_terms", "updated_at"])

        response = self.client.get(reverse("assistant_linking:normalization_parsed"))

        self.assertEqual(response.status_code, 200)
        parses = list(response.context["parses"])
        self.assertEqual(len(parses), 2)
        self.assertTrue(all("tester" not in parsed.supplier_product.name.lower() for parsed in parses))

    def test_unparsed_page_uses_50_row_pagination(self):
        for index in range(60):
            SupplierProduct.objects.create(
                supplier=self.supplier,
                identity_key=f"unparsed-page-{index}",
                name=f"Queue Product {index}",
            )

        response = self.client.get(reverse("assistant_linking:normalization_unparsed"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.per_page, 50)
