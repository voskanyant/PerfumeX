from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase

from assistant_core.models import GlobalRule
from assistant_linking.models import BrandAlias, ConcentrationAlias, ParsedSupplierProduct, ProductAlias
from assistant_linking.services.normalizer import parse_supplier_product, save_parse
from catalog.models import Brand
from prices.models import Supplier, SupplierProduct


class NormalizerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.supplier = Supplier.objects.create(name="Supplier", code="sup")
        self.brand = Brand.objects.create(name="Dolce Gabbana")
        BrandAlias.objects.create(brand=self.brand, alias_text="DG", normalized_alias="dg")
        GlobalRule.objects.bulk_create(
            [
                GlobalRule(
                    title="regex_preprocess: eau de perfume",
                    rule_kind="regex_preprocess",
                    scope_type="global",
                    rule_text=r"\beau de perfume\b => eau de parfum",
                    approved=True,
                    active=True,
                ),
                GlobalRule(
                    title="regex_preprocess: eau de parfume",
                    rule_kind="regex_preprocess",
                    scope_type="global",
                    rule_text=r"\beau de parfume\b => eau de parfum",
                    approved=True,
                    active=True,
                ),
                GlobalRule(
                    title="Parser mini terms",
                    rule_kind="parser_mini_term",
                    scope_type="global",
                    rule_text="miniature",
                    approved=True,
                    active=True,
                ),
                GlobalRule(
                    title="Parser refill terms",
                    rule_kind="parser_refill_term",
                    scope_type="global",
                    rule_text="refill",
                    approved=True,
                    active=True,
                ),
                GlobalRule(
                    title="Garbage keyword: fake",
                    rule_kind="garbage_keyword",
                    scope_type="global",
                    rule_text="fake",
                    approved=True,
                    active=True,
                ),
            ]
        )
        cache.clear()

    def test_parses_concentration_size_tester_and_gender(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="1",
            name="DG Light Blue EDT pour Homme tester 3.4 oz",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, 100)
        self.assertTrue(parsed.is_tester)
        self.assertEqual(parsed.supplier_gender_hint, "Pour Homme")
        self.assertEqual(parsed.normalized_brand, self.brand)

    def test_parses_decimal_ml_with_comma_or_dot(self):
        brand = Brand.objects.create(name="Tiziana Terenzi")
        BrandAlias.objects.create(brand=brand, alias_text="Tiziana Terenzi", normalized_alias="tiziana terenzi")
        examples = (
            ("TIZIANA TERENZI CABIRIA EDP 1,5 ML", Decimal("1.50")),
            ("TIZIANA TERENZI CABIRIA EDP 1.5 ML", Decimal("1.50")),
            ("TIZIANA TERENZI CABIRIA EDP 7.5ML", Decimal("7.50")),
        )

        for name, expected_size in examples:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=name,
                    name=name,
                )

                parsed = parse_supplier_product(product)

                self.assertEqual(parsed.concentration, "Eau de Parfum")
                self.assertEqual(parsed.size_ml, expected_size)
                self.assertEqual(parsed.product_name_text, "cabiria")

    def test_catalog_variant_does_not_override_explicit_supplier_size(self):
        brand = Brand.objects.create(name="Tiziana Terenzi")
        BrandAlias.objects.create(brand=brand, alias_text="Tiziana Terenzi", normalized_alias="tiziana terenzi")
        perfume = brand.perfumes.create(name="Cabiria", concentration="Extrait de Parfum")
        variant = perfume.variants.create(size_ml=Decimal("5.00"), variant_type="standard")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="cabiria-linked-decimal",
            name="TIZIANA TERENZI CABIRIA EDP 1,5 ML",
            catalog_perfume=perfume,
            catalog_variant=variant,
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.product_name_text, "Cabiria")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("1.50"))

    def test_parses_multi_pack_sizes_as_set_size_label(self):
        brand = Brand.objects.create(name="Vilhelm Parfumerie")
        BrandAlias.objects.create(brand=brand, alias_text="Vilhelm Parfumerie", normalized_alias="vilhelm parfumerie")
        examples = (
            ("Vilhelm Parfumerie MODEST MIMOSA edp 3 x 10ml", "3*10ml", Decimal("10.00")),
            ("Vilhelm Parfumerie MODEST MIMOSA edp 3*10ml", "3*10ml", Decimal("10.00")),
            ("Vilhelm Parfumerie MODEST MIMOSA edp 5 * 7,5 ml", "5*7.5ml", Decimal("7.50")),
            ("Vilhelm Parfumerie MODEST MIMOSA edp 5x7.5", "5*7.5ml", Decimal("7.50")),
        )

        for name, expected_label, expected_size in examples:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=name,
                    name=name,
                )

                parsed = save_parse(product, force=True)

                self.assertEqual(parsed.concentration, "Eau de Parfum")
                self.assertEqual(parsed.size_ml, expected_size)
                self.assertEqual(parsed.raw_size_text, expected_label)
                self.assertEqual(parsed.display_size, expected_label)
                self.assertTrue(parsed.is_set)
                self.assertEqual(parsed.variant_type, "set")
                self.assertEqual(
                    parsed.display_identity,
                    f"Vilhelm Parfumerie / Modest Mimosa / Eau de Parfum / {expected_label} / Set",
                )

    def test_russian_hair_mist_beats_linked_perfume_concentration(self):
        brand = Brand.objects.create(name="Givenchy")
        BrandAlias.objects.create(brand=brand, alias_text="Givenchy", normalized_alias="givenchy")
        perfume = brand.perfumes.create(name="L'Interdit", concentration="Eau de Toilette")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="givenchy-hair-mist",
            name="Givenchy L'INTERDIT 35ml дымка для волос TESTER",
            catalog_perfume=perfume,
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.product_name_text, "L'Interdit")
        self.assertEqual(parsed.concentration, "Hair Perfume")
        self.assertEqual(parsed.size_ml, Decimal("35.00"))
        self.assertTrue(parsed.is_tester)
        self.assertEqual(parsed.display_variant_type, "Tester")
        self.assertEqual(parsed.product_category_label, "Hair Care")
        self.assertEqual(parsed.display_identity, "Givenchy / L'Interdit / Hair Perfume / 35ml / Tester")

    def test_english_hair_mist_and_hair_perfume_keep_supplier_form(self):
        brand = Brand.objects.create(name="Givenchy")
        BrandAlias.objects.create(brand=brand, alias_text="Givenchy", normalized_alias="givenchy")
        examples = (
            ("Givenchy L'Interdit hair mist 35ml", "Hair Mist"),
            ("Givenchy L'Interdit hair perfume 35ml", "Hair Perfume"),
        )

        for name, expected_concentration in examples:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=name,
                    name=name,
                )

                parsed = save_parse(product, force=True)

                self.assertEqual(parsed.concentration, expected_concentration)
                self.assertEqual(parsed.product_category_label, "Hair Care")
                self.assertEqual(parsed.size_ml, Decimal("35.00"))

    def test_standalone_w_and_m_are_audience_aliases_not_product_name(self):
        brand = Brand.objects.create(name="Abercrombie & Fitch")
        BrandAlias.objects.create(brand=brand, alias_text="Abercrombie Fitch", normalized_alias="abercrombie fitch")
        woman_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="audience-w",
            name="Abercrombie Fitch Authentic Moment w tester edp100ml",
        )
        men_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="audience-m",
            name="Abercrombie Fitch Authentic m tester edt100ml",
        )

        woman_parse = parse_supplier_product(woman_product)
        men_parse = parse_supplier_product(men_product)

        self.assertEqual(woman_parse.supplier_gender_hint, "Woman")
        self.assertEqual(woman_parse.product_name_text, "authentic moment")
        self.assertTrue(woman_parse.is_tester)
        self.assertEqual(men_parse.supplier_gender_hint, "Men")
        self.assertEqual(men_parse.product_name_text, "authentic")

    def test_display_identity_title_cases_scent_but_keeps_joiners_lowercase(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="title-case",
            name="Example",
        )
        parsed = ParsedSupplierProduct.objects.create(
            supplier_product=product,
            raw_name=product.name,
            normalized_text="example",
            detected_brand_text="Byredo",
            product_name_text="rose of no man's land in bloom",
            concentration="Eau de Parfum",
            size_ml="100",
        )

        self.assertEqual(parsed.display_product_name, "Rose of No Man's Land in Bloom")
        self.assertEqual(parsed.display_identity, "Byredo / Rose of No Man's Land in Bloom / Eau de Parfum / 100ml")

    def test_femme_keeps_supplier_style_but_matches_women_group(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="audience-femme",
            name="DG Light Blue pour femme edt 100ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.supplier_gender_hint, "Pour Femme")
        self.assertEqual(parsed.product_name_text, "light blue")

    def test_explicit_edp_wins_over_catalogue_link_concentration(self):
        brand = Brand.objects.create(name="Trussardi")
        BrandAlias.objects.create(brand=brand, alias_text="Trussardi", normalized_alias="trussardi")
        perfume = Brand.objects.get(name="Trussardi").perfumes.create(
            name="Donna",
            concentration="Eau de Toilette",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="trussardi-donna-edp",
            name="Trussardi Donna edp 100ml",
            catalog_perfume=perfume,
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Donna")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.supplier_gender_hint, "Woman")

    def test_locked_human_parse_is_not_overwritten(self):
        product = SupplierProduct.objects.create(supplier=self.supplier, identity_key="2", name="DG Light Blue EDP 100ml")
        parsed = save_parse(product)
        parsed.locked_by_human = True
        parsed.product_name_text = "Human value"
        parsed.save()

        again = save_parse(product)

        self.assertEqual(again.product_name_text, "Human value")

    def test_product_alias_must_match_whole_phrase(self):
        brand = Brand.objects.create(name="12 Parfumeurs")
        BrandAlias.objects.create(brand=brand, alias_text="12 Parfumeurs", normalized_alias="12 parfumeurs")
        ProductAlias.objects.create(
            brand=brand,
            alias_text="O",
            canonical_text="O",
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="3",
            name="12 Parfumeurs Malmaison 100ml EDP",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "malmaison")

    def test_compact_concentration_and_size_are_split(self):
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(brand=brand, alias_text="Montale", normalized_alias="montale")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="4",
            name="Montale Tropical Wood tester edp100ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "tropical wood")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, 100)
        self.assertTrue(parsed.is_tester)

    def test_bare_trailing_size_is_inferred_after_brand_and_concentration(self):
        brand = Brand.objects.create(name="100 Bon")
        BrandAlias.objects.create(brand=brand, alias_text="100 BON", normalized_alias="100 bon")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="5",
            name="100 BON BOIS DE MANGROVE 50 EDP",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "bois de mangrove")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, 50)

    def test_compact_size_before_concentration_is_split(self):
        brand = Brand.objects.create(name="24K")
        BrandAlias.objects.create(brand=brand, alias_text="24K", normalized_alias="24k")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="5a",
            name="24K SUPREME ROUGE 100edp TESTER",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "supreme rouge")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, 100)
        self.assertTrue(parsed.is_tester)

    def test_reversed_ml_size_is_parsed(self):
        cases = (
            ("reversed-ml-latin", "1916 Agua De Colonia Limon & Tonca ml 100 tester"),
            ("reversed-ml-cyrillic", "1916 Agua De Colonia Limon & Tonca мл 100 тестер"),
        )
        for identity_key, name in cases:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=identity_key,
                    name=name,
                )

                parsed = parse_supplier_product(product)

                self.assertEqual(parsed.size_ml, 100)
                self.assertTrue(parsed.is_tester)

    def test_kb_regex_preprocess_handles_eau_de_perfume_as_eau_de_parfum(self):
        for index, raw in enumerate(("eau de perfume", "eau de parfume", "eau de parf"), start=1):
            product = SupplierProduct.objects.create(
                supplier=self.supplier,
                identity_key=f"eau-perfume-{index}",
                name=f"Some Brand Scent {raw} 100ml",
            )

            parsed = parse_supplier_product(product)

            self.assertEqual(parsed.concentration, "Eau de Parfum")
            self.assertEqual(parsed.size_ml, 100)

    def test_bare_perfume_and_parfume_mean_extrait(self):
        for index, raw in enumerate(("perfume", "parfume"), start=1):
            product = SupplierProduct.objects.create(
                supplier=self.supplier,
                identity_key=f"bare-perfume-{index}",
                name=f"Some Brand Scent {raw} 100ml",
            )

            parsed = parse_supplier_product(product)

            self.assertEqual(parsed.concentration, "Extrait de Parfum")
            self.assertEqual(parsed.size_ml, 100)

    def test_miniature_is_mini_not_travel(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="miniature",
            name="Some Brand Scent miniature 10ml",
        )

        parsed = parse_supplier_product(product)

        self.assertFalse(parsed.is_travel)
        self.assertEqual(parsed.variant_type, "mini")
        self.assertIn("mini", parsed.modifiers)

    def test_rejected_sample_words_do_not_mark_sample(self):
        for index, raw in enumerate(("decant", "отливант", "разлив", "split"), start=1):
            product = SupplierProduct.objects.create(
                supplier=self.supplier,
                identity_key=f"not-sample-{index}",
                name=f"Some Brand Scent {raw} 10ml",
            )

            parsed = parse_supplier_product(product)

            self.assertFalse(parsed.is_sample)

    def test_refill_terms_add_refill_modifier(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="refill",
            name="Some Brand Scent refill 100ml",
        )

        parsed = parse_supplier_product(product)

        self.assertIn("refill", parsed.modifiers)

    def test_damage_terms_route_to_garbage_but_decode_does_not(self):
        damaged = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="damaged",
            name="Some Brand Scent fake 100ml",
        )
        decoded = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="decoded",
            name="Some Brand Scent декод 100ml",
        )

        damaged_parse = parse_supplier_product(damaged)
        decoded_parse = parse_supplier_product(decoded)

        self.assertEqual(damaged_parse.modifiers, ["garbage"])
        self.assertNotEqual(decoded_parse.modifiers, ["garbage"])

    def test_compact_decimal_and_russian_size_formats_are_normalized(self):
        decimal_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="compact-decimal",
            name="Foo 100.0ml",
        )
        russian_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="compact-russian",
            name="Foo 100мл",
        )

        self.assertEqual(parse_supplier_product(decimal_product).size_ml, 100)
        self.assertEqual(parse_supplier_product(russian_product).size_ml, 100)

    def test_no_five_is_not_treated_as_size(self):
        brand = Brand.objects.create(name="Chanel")
        BrandAlias.objects.create(brand=brand, alias_text="Chanel", normalized_alias="chanel")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="6",
            name="Chanel No 5 Eau de Parfum",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertIsNone(parsed.size_ml)

    def test_garbage_keyword_excludes_row_from_normalization(self):
        GlobalRule.objects.create(
            title="Garbage keyword: blotters",
            rule_kind="garbage_keyword",
            scope_type="global",
            rule_text="blotters",
            active=True,
            approved=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="6a",
            name="Escentric Molecules blotters 20pcs",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.modifiers, ["garbage"])
        self.assertEqual(parsed.confidence, 100)
        self.assertIn("excluded garbage keyword: blotters", parsed.warnings)
        self.assertFalse(parsed.product_name_text)

    def test_custom_concentration_aliases_can_be_managed_in_database(self):
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(brand=brand, alias_text="Montale", normalized_alias="montale")
        ConcentrationAlias.objects.create(
            concentration="Eau de Parfum",
            alias_text="парфюмированная вода",
            normalized_alias="парфюмированная вода",
            priority=40,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="7",
            name="Montale Arabians Tonka парфюмированная вода 100ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.concentration, "Eau de Parfum")

    def test_russian_concentration_tester_size_and_unisex_terms_are_normalized(self):
        brand = Brand.objects.create(name="100 Bon")
        BrandAlias.objects.create(brand=brand, alias_text="100 Bon", normalized_alias="100 bon")
        ConcentrationAlias.objects.create(
            concentration="Eau de Parfum",
            alias_text="парфюмированная вода",
            normalized_alias="парфюмированная вода",
            priority=40,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="ru-1",
            name="100 Bon Ambre and Tonka парфюмированная вода тестер 50 м.л. уни",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, 50)
        self.assertTrue(parsed.is_tester)
        self.assertEqual(parsed.variant_type, "tester")
        self.assertEqual(parsed.supplier_gender_hint, "Unisex")
        self.assertEqual(parsed.product_name_text, "ambre and tonka")

    def test_builtin_russian_concentration_aliases_work_without_database_seed(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="ru-default-concentration",
            name="Some Brand Scent парфюмерная вода 50мл",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, 50)

    def test_builtin_russian_oil_aliases_work_without_database_seed(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="ru-oil-concentration",
            name="Some Brand Scent масляные духи 10мл",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.concentration, "Perfume Oil")
        self.assertEqual(parsed.size_ml, 10)

    def test_brand_alias_rejects_bad_regex(self):
        alias = BrandAlias(
            brand=self.brand,
            alias_text="bad regex",
            normalized_alias="(",
            is_regex=True,
        )

        with self.assertRaises(ValidationError) as ctx:
            alias.full_clean()

        self.assertIn("Invalid regex", str(ctx.exception))

    def test_brand_alias_rejects_redos_shape(self):
        alias = BrandAlias(
            brand=self.brand,
            alias_text="bad regex",
            normalized_alias=r"(.+)+",
            is_regex=True,
        )

        with self.assertRaises(ValidationError) as ctx:
            alias.full_clean()

        self.assertIn("catastrophic-backtracking shape", str(ctx.exception))

    @patch("assistant_linking.services.normalizer.mail_admins")
    @patch("assistant_linking.services.normalizer.regex.search", side_effect=TimeoutError)
    def test_normalizer_skips_alias_on_regex_timeout(self, mock_search, mock_mail_admins):
        brand = Brand.objects.create(name="Timeout Brand")
        alias = BrandAlias.objects.create(
            brand=brand,
            alias_text="timeout",
            normalized_alias="timeout",
            is_regex=True,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="timeout",
            name="timeout scent 100ml",
        )

        parsed = parse_supplier_product(product)

        alias.refresh_from_db()
        self.assertFalse(alias.active)
        self.assertIsNone(parsed.normalized_brand)
        mock_search.assert_called()
        mock_mail_admins.assert_called_once()
