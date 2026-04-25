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

    def test_product_alias_can_override_wrong_supplier_concentration(self):
        brand = Brand.objects.create(name="12 Parfumeurs")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="12 parfumeurs",
            normalized_alias="12 parfumeurs",
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="malmaison",
            canonical_text="Malmaison",
            concentration="Extrait de Parfum",
            priority=40,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="malmaison-1",
            name="12 Parfumeurs Malmaison 100ml EDP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Malmaison")
        self.assertEqual(parsed.concentration, "Extrait de Parfum")

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

    def test_catalog_link_keeps_canonical_concentration_on_reparse(self):
        brand = Brand.objects.create(name="12 Parfumeurs")
        perfume = Perfume.objects.create(brand=brand, name="Malmaison", concentration="Extrait de Parfum")
        variant = PerfumeVariant.objects.create(perfume=perfume, size_ml="100", variant_type="standard")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="12 parfumeurs",
            normalized_alias="12 parfumeurs",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="malmaison-linked",
            name="12 Parfumeurs Malmaison 100ml EDP",
            catalog_perfume=perfume,
            catalog_variant=variant,
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Malmaison")
        self.assertEqual(parsed.concentration, "Extrait de Parfum")
        self.assertEqual(parsed.size_ml, variant.size_ml)

    def test_normalization_detail_prefills_teaching_from_catalog_link(self):
        brand = Brand.objects.create(name="12 Parfumeurs")
        perfume = Perfume.objects.create(brand=brand, name="Malmaison", concentration="Extrait de Parfum")
        variant = PerfumeVariant.objects.create(perfume=perfume, size_ml="100", variant_type="standard")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="malmaison-detail",
            name="12 Parfumeurs Malmaison 100ml EDP",
            catalog_perfume=perfume,
            catalog_variant=variant,
        )

        response = self.client.get(reverse("assistant_linking:normalization_detail", args=[product.id]))

        self.assertEqual(response.status_code, 200)
        form = response.context["teach_form"]
        self.assertEqual(form["brand_name"].value(), "12 Parfumeurs")
        self.assertEqual(form["product_name"].value(), "Malmaison")
        self.assertEqual(form["concentration"].value(), "Extrait de Parfum")
        self.assertEqual(str(form["size_ml"].value()), "100")

    def test_normalization_detail_prefills_teaching_from_strong_catalog_conflict(self):
        brand = Brand.objects.create(name="12 Parfumeurs")
        perfume = Perfume.objects.create(brand=brand, name="Malmaison", concentration="Extrait de Parfum")
        PerfumeVariant.objects.create(perfume=perfume, size_ml="100", variant_type="standard")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="12 parfumeurs",
            normalized_alias="12 parfumeurs",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="malmaison-candidate",
            name="12 Parfumeurs Malmaison 100ml EDP",
        )

        response = self.client.get(reverse("assistant_linking:normalization_detail", args=[product.id]))

        self.assertEqual(response.status_code, 200)
        parsed = response.context["parsed"]
        form = response.context["teach_form"]
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(form["concentration"].value(), "Extrait de Parfum")
        self.assertContains(response, "Catalogue match suggests Extrait de Parfum")

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
        parse_product_ids = [item.supplier_product_id for item in response.context["parses"]]
        self.assertNotIn(product.id, parse_product_ids)
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

    def test_parsed_products_page_shows_tester_in_identity(self):
        brand = Brand.objects.create(name="100 Bon")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="100 bon",
            normalized_alias="100 bon",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="tester-display",
            name="100 Bon Ambre Sensuel 50ml edt TESTER",
        )
        save_parse(product)

        response = self.client.get(reverse("assistant_linking:normalization_parsed"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100 Bon / ambre sensuel / Eau de Toilette / 50.00 / Tester")

    def test_normalization_detail_capitalizes_tester_type(self):
        brand = Brand.objects.create(name="100 Bon")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="100 bon",
            normalized_alias="100 bon",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="tester-detail",
            name="100 Bon Ambre Sensuel 50ml edt TESTER",
        )
        save_parse(product)

        response = self.client.get(reverse("assistant_linking:normalization_detail", args=[product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Type: Tester")
        self.assertContains(response, "Normalized: 100 Bon / ambre sensuel / Eau de Toilette / 50.00 / Tester")

    def test_parsed_products_page_requires_complete_parse_fields(self):
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="montale",
            normalized_alias="montale",
        )
        complete = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="complete-parse",
            name="Montale Arabians Tonka EDP 100ml",
        )
        missing_size = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="missing-size-parse",
            name="Montale Arabians Tonka EDP",
        )
        missing_concentration = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="missing-concentration-parse",
            name="Montale Arabians Tonka 100ml",
        )
        missing_name = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="missing-name-parse",
            name="Montale EDP 100ml",
        )
        for product in [complete, missing_size, missing_concentration, missing_name]:
            save_parse(product)

        response = self.client.get(reverse("assistant_linking:normalization_parsed"))

        self.assertEqual(response.status_code, 200)
        parse_product_ids = {item.supplier_product_id for item in response.context["parses"]}
        self.assertIn(complete.id, parse_product_ids)
        self.assertNotIn(missing_size.id, parse_product_ids)
        self.assertNotIn(missing_concentration.id, parse_product_ids)
        self.assertNotIn(missing_name.id, parse_product_ids)

    def test_set_rows_are_not_complete_parsed_products(self):
        brand = Brand.objects.create(name="Abercrombie & Fitch")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="abercrombie fitch",
            normalized_alias="abercrombie fitch",
        )
        set_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="set-row",
            name="Abercrombie Fitch First Instinct Woman набор 2пр edp100ml+200л",
        )
        regular_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="regular-row",
            name="Abercrombie Fitch First Instinct Woman edp100ml",
        )
        set_parse = save_parse(set_product)
        save_parse(regular_product)

        parsed_response = self.client.get(reverse("assistant_linking:normalization_parsed"))
        set_response = self.client.get(reverse("assistant_linking:normalization_sets"))

        self.assertTrue(set_parse.is_set)
        self.assertEqual(parsed_response.status_code, 200)
        parsed_product_ids = {item.supplier_product_id for item in parsed_response.context["parses"]}
        self.assertNotIn(set_product.id, parsed_product_ids)
        self.assertIn(regular_product.id, parsed_product_ids)
        self.assertEqual(set_response.status_code, 200)
        set_product_ids = {item.supplier_product_id for item in set_response.context["parses"]}
        self.assertIn(set_product.id, set_product_ids)
        self.assertNotIn(regular_product.id, set_product_ids)

    def test_dashboard_counts_only_complete_rows_as_parsed(self):
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="montale",
            normalized_alias="montale",
        )
        complete = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dashboard-complete",
            name="Montale Arabians Tonka EDP 100ml",
        )
        missing_size = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dashboard-missing-size",
            name="Montale Arabians Tonka EDP",
        )
        missing_concentration = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dashboard-missing-concentration",
            name="Montale Arabians Tonka 100ml",
        )
        missing_name = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dashboard-missing-name",
            name="Montale EDP 100ml",
        )
        set_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dashboard-set",
            name="Montale Arabians Tonka gift set EDP 100ml",
        )
        for product in [complete, missing_size, missing_concentration, missing_name, set_product]:
            save_parse(product)

        response = self.client.get(reverse("assistant_linking:normalization_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["parsed_count"], 1)
        self.assertEqual(response.context["set_count"], 1)
        self.assertEqual(response.context["missing_size_count"], 1)
        self.assertEqual(response.context["missing_concentration_count"], 1)
        self.assertEqual(response.context["missing_name_count"], 1)

    def test_missing_concentration_and_name_queues_are_searchable(self):
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="montale",
            normalized_alias="montale",
        )
        missing_concentration = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="queue-missing-concentration",
            name="Montale Arabians Tonka 100ml",
        )
        missing_name = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="queue-missing-name",
            name="Montale EDP 100ml",
        )
        save_parse(missing_concentration)
        save_parse(missing_name)

        concentration_response = self.client.get(
            reverse("assistant_linking:normalization_missing_concentration"),
            {"q": "Arabians"},
        )
        name_response = self.client.get(
            reverse("assistant_linking:normalization_missing_name"),
            {"q": "Montale"},
        )

        self.assertEqual(concentration_response.status_code, 200)
        self.assertEqual(name_response.status_code, 200)
        concentration_ids = {item.supplier_product_id for item in concentration_response.context["parses"]}
        name_ids = {item.supplier_product_id for item in name_response.context["parses"]}
        self.assertIn(missing_concentration.id, concentration_ids)
        self.assertIn(missing_name.id, name_ids)

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
        self.assertEqual(str(tester_variant), "V Canto / Ricina / 100ml / Tester")

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
        self.assertEqual(parsed_one.normalized_brand, target_brand)
        self.assertIsNone(parsed_two.normalized_brand)
        self.assertNotEqual(parsed_one.product_name_text, "Romeo on the Rocks")
        self.assertNotEqual(parsed_two.product_name_text, "Romeo on the Rocks")

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
        self.assertEqual(parsed_one.product_name_text, "Romeo on the Rocks")
        self.assertIsNone(parsed_two.normalized_brand)
        self.assertNotEqual(parsed_two.product_name_text, "Romeo on the Rocks")

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
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="montale",
            normalized_alias="montale",
        )
        hidden = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="hidden-1",
            name="Montale Hidden Tester EDP 100ml",
        )
        visible = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="visible-1",
            name="Montale Arabians Tonka EDP 100ml",
        )
        save_parse(hidden)
        save_parse(visible)
        prefs = UserPreference.get_for_user(self.user)
        prefs.supplier_exclude_terms = "tester"
        prefs.save(update_fields=["supplier_exclude_terms", "updated_at"])

        response = self.client.get(reverse("assistant_linking:normalization_parsed"))

        self.assertEqual(response.status_code, 200)
        parses = list(response.context["parses"])
        self.assertEqual(len(parses), 1)
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
