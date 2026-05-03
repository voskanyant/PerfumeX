from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase

from assistant_core.models import GlobalRule
from assistant_linking.models import (
    BAG_MODIFIER,
    COSMETIC_PUDRE_MODIFIER,
    DECANT_MODIFIER,
    DEODORANT_MODIFIER,
    MANUAL_REVIEW_MODIFIER,
    VINTAGE_MODIFIER,
    BrandAlias,
    ConcentrationAlias,
    ParsedSupplierProduct,
    ProductAlias,
)
from assistant_linking.services.normalizer import parse_supplier_product, save_parse
from assistant_linking.utils.text import normalize_alias_value
from catalog.models import Brand
from prices.models import Supplier, SupplierProduct


class NormalizerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.supplier = Supplier.objects.create(name="Supplier", code="sup")
        self.brand = Brand.objects.create(name="Dolce Gabbana")
        BrandAlias.objects.create(
            brand=self.brand, alias_text="DG", normalized_alias="dg"
        )
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
        self.assertEqual(parsed.product_name_text, "light blue pour homme")

    def test_brand_alias_matching_ignores_ampersands_and_non_decimal_dots(self):
        abercrombie = Brand.objects.create(name="Abercrombie & Fitch")
        banderas = Brand.objects.create(name="Antonio Banderas")
        BrandAlias.objects.create(
            brand=abercrombie,
            alias_text="Аберкромби & Фитч",
            normalized_alias=normalize_alias_value("Аберкромби & Фитч"),
            priority=35,
        )
        BrandAlias.objects.create(
            brand=banderas,
            alias_text="АНТ. БАН.",
            normalized_alias=normalize_alias_value("АНТ. БАН."),
            priority=35,
        )
        examples = (
            ("abercrombie", "Аберкромби Фитч Fierce EDT 50ml", abercrombie),
            ("banderas", "АНТ БАН The Secret EDT 100ml", banderas),
        )

        for identity_key, name, expected_brand in examples:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=identity_key,
                    name=name,
                )

                parsed = parse_supplier_product(product)

                self.assertEqual(parsed.normalized_brand, expected_brand)

    def test_parses_decimal_ml_with_comma_or_dot(self):
        brand = Brand.objects.create(name="Tiziana Terenzi")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Tiziana Terenzi",
            normalized_alias="tiziana terenzi",
        )
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
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Tiziana Terenzi",
            normalized_alias="tiziana terenzi",
        )
        perfume = brand.perfumes.create(
            name="Cabiria", concentration="Extrait de Parfum"
        )
        variant = perfume.variants.create(
            size_ml=Decimal("5.00"), variant_type="standard"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="cabiria-linked-decimal",
            name="TIZIANA TERENZI CABIRIA EDP 1,5 ML",
            catalog_perfume=perfume,
            catalog_variant=variant,
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.product_name_text, "Cabiria")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("1.50"))

    def test_global_product_alias_does_not_override_explicit_supplier_concentration(
        self,
    ):
        brand = Brand.objects.create(name="Nina Ricci")
        BrandAlias.objects.create(
            brand=brand, alias_text="Nina Ricci", normalized_alias="nina ricci"
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="nina",
            canonical_text="Nina",
            concentration="Extrait de Parfum",
            priority=40,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="nina-edt",
            name="Nina Ricci NINA 50ml edt tester",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.product_name_text, "Nina")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))
        self.assertTrue(parsed.is_tester)

    def test_product_alias_does_not_invent_missing_supplier_concentration(self):
        brand = Brand.objects.create(name="Francis Kurkdjian")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Francis Kurkdjian",
            normalized_alias="francis kurkdjian",
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="a la rose",
            canonical_text="A La Rose",
            concentration="Eau de Parfum",
            priority=40,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="shower-cream-no-concentration",
            name="Francis Kurkdjian A La Rose Shower Cream 250ml Tester",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.product_name_text, "A La Rose")
        self.assertEqual(parsed.concentration, "")
        self.assertIn("concentration missing", parsed.warnings)
        self.assertEqual(parsed.size_ml, Decimal("250.00"))
        self.assertTrue(parsed.is_tester)

    def test_parses_multi_pack_sizes_as_set_size_label(self):
        brand = Brand.objects.create(name="Vilhelm Parfumerie")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Vilhelm Parfumerie",
            normalized_alias="vilhelm parfumerie",
        )
        examples = (
            (
                "Vilhelm Parfumerie MODEST MIMOSA edp 3 x 10ml",
                "3*10ml",
                Decimal("10.00"),
            ),
            ("Vilhelm Parfumerie MODEST MIMOSA edp 3*10ml", "3*10ml", Decimal("10.00")),
            (
                "Vilhelm Parfumerie MODEST MIMOSA edp 5 * 7,5 ml",
                "5*7.5ml",
                Decimal("7.50"),
            ),
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
        BrandAlias.objects.create(
            brand=brand, alias_text="Givenchy", normalized_alias="givenchy"
        )
        perfume = brand.perfumes.create(
            name="L'Interdit", concentration="Eau de Toilette"
        )
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
        self.assertEqual(
            parsed.display_identity,
            "Givenchy / L'Interdit / Hair Perfume / 35ml / Tester",
        )

    def test_english_hair_mist_and_hair_perfume_keep_supplier_form(self):
        brand = Brand.objects.create(name="Givenchy")
        BrandAlias.objects.create(
            brand=brand, alias_text="Givenchy", normalized_alias="givenchy"
        )
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

    def test_russian_paket_routes_to_bags_category(self):
        BrandAlias.objects.create(
            brand=self.brand,
            alias_text="Dolce&Gabbana",
            normalized_alias="dolce&gabbana",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dolce-bag",
            name="Dolce&Gabbana ПАКЕТ (черный) 19.5*8.5*13*",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, self.brand)
        self.assertEqual(parsed.product_category_label, "Bags")
        self.assertEqual(parsed.variant_type, BAG_MODIFIER)
        self.assertEqual(parsed.display_variant_type, "Bag")
        self.assertIn(BAG_MODIFIER, parsed.modifiers)
        self.assertIsNone(parsed.size_ml)
        self.assertEqual(parsed.raw_size_text, "")
        self.assertNotIn("concentration missing", parsed.warnings)
        self.assertNotIn("size ambiguous", parsed.warnings)
        self.assertNotIn("gender missing", parsed.warnings)

    def test_russian_pudra_routes_to_cosmetics_poudre(self):
        brand = Brand.objects.create(name="Dior")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Dior",
            normalized_alias="dior",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dior-poudre",
            name="Dior Пудра 01",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_category_label, "Cosmetics")
        self.assertEqual(parsed.product_subcategory_label, "Poudre")
        self.assertEqual(parsed.variant_type, "poudre")
        self.assertEqual(parsed.display_variant_type, "Poudre")
        self.assertIn(COSMETIC_PUDRE_MODIFIER, parsed.modifiers)
        self.assertNotIn("concentration missing", parsed.warnings)
        self.assertNotIn("size ambiguous", parsed.warnings)
        self.assertNotIn("gender missing", parsed.warnings)

    def test_deodorant_without_concentration_routes_to_deodorants(self):
        brand = Brand.objects.create(name="Chanel")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Chanel",
            normalized_alias="chanel",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="chanel-deodorant",
            name="Chanel Bleu Deodorant Spray 100ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_category_label, "Deodorants")
        self.assertEqual(parsed.variant_type, DEODORANT_MODIFIER)
        self.assertEqual(parsed.display_variant_type, "Deodorant")
        self.assertIn(DEODORANT_MODIFIER, parsed.modifiers)
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertNotIn("concentration missing", parsed.warnings)
        self.assertNotIn("size ambiguous", parsed.warnings)
        self.assertNotIn("gender missing", parsed.warnings)

    def test_decant_terms_route_to_decants_and_display_decant_suffix(self):
        brand = Brand.objects.create(name="Zarkoperfume")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Zarkoperfume",
            normalized_alias="zarkoperfume",
        )
        examples = (
            (
                "zarko-the-muse-decant",
                "Zarkoperfume the muse edp 10ml \u043e\u0442\u043b\u0438\u0432",
                "the muse",
                Decimal("10.00"),
            ),
            (
                "zarko-purple-molecule-short-decant",
                "Zarkoperfume PURPLE MOLECULE 070.07 5ml edp \u043e\u0442\u043b",
                "purple molecule 070.07",
                Decimal("5.00"),
            ),
            (
                "zarko-cloud-uppercase-decant",
                "Zarkoperfume CLOUD COLLECTION No.3 2ml edp \u041e\u0422\u041b\u0418\u0412\u0410",
                "cloud collection no.3",
                Decimal("2.00"),
            ),
        )

        for identity_key, name, expected_name, expected_size in examples:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=identity_key,
                    name=name,
                )

                parsed = save_parse(product, force=True)

                self.assertEqual(parsed.normalized_brand, brand)
                self.assertEqual(parsed.product_category_label, "Decants")
                self.assertEqual(parsed.product_name_text, expected_name)
                self.assertEqual(parsed.concentration, "Eau de Parfum")
                self.assertEqual(parsed.size_ml, expected_size)
                self.assertEqual(parsed.variant_type, DECANT_MODIFIER)
                self.assertEqual(parsed.display_variant_type, "Decant")
                self.assertIn(DECANT_MODIFIER, parsed.modifiers)
                self.assertNotIn("gender missing", parsed.warnings)
                self.assertTrue(parsed.display_identity.endswith(" / Decant"))

    def test_vintage_terms_route_to_vintage_and_display_vintage_suffix(self):
        brand = Brand.objects.create(name="Chanel")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Chanel",
            normalized_alias="chanel",
        )
        examples = (
            (
                "chanel-no5-vintage",
                "Chanel No 5 edp 50ml vintage",
                "no 5",
            ),
            (
                "chanel-coco-cyrillic-vintage",
                "Chanel Coco edt 100ml \u0432\u0438\u043d\u0442\u0430\u0436",
                "coco",
            ),
            (
                "chanel-chance-short-vint",
                "Chanel Chance edt 35ml vint",
                "chance",
            ),
        )

        for identity_key, name, expected_name in examples:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=identity_key,
                    name=name,
                )

                parsed = save_parse(product, force=True)

                self.assertEqual(parsed.normalized_brand, brand)
                self.assertEqual(parsed.product_category_label, "Vintage")
                self.assertEqual(parsed.product_name_text, expected_name)
                self.assertEqual(parsed.variant_type, VINTAGE_MODIFIER)
                self.assertEqual(parsed.display_variant_type, "Vintage")
                self.assertIn(VINTAGE_MODIFIER, parsed.modifiers)
                self.assertNotIn("gender missing", parsed.warnings)
                self.assertTrue(parsed.display_identity.endswith(" / Vintage"))

    def test_deodorant_word_with_concentration_stays_perfume(self):
        brand = Brand.objects.create(name="Chanel")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Chanel",
            normalized_alias="chanel",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="chanel-deodorant-edp",
            name="Chanel Bleu Deodorant edp 100ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.product_category_label, "Perfume")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertNotEqual(parsed.variant_type, DEODORANT_MODIFIER)
        self.assertNotIn(DEODORANT_MODIFIER, parsed.modifiers)

    def test_generated_person_brand_alias_matches_initial_and_last_name(self):
        brand = Brand.objects.create(name="Antonio Banderas")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="antonio-banderas-abbrev",
            name="A.Banderas The Secret Game 80ml edt",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.detected_brand_text, "a.banderas")
        self.assertEqual(parsed.product_name_text, "the secret game")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("80.00"))

    def test_configured_brand_alias_allows_spacing_after_initial_dot(self):
        brand = Brand.objects.create(name="Dior")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="c.dior",
            normalized_alias="c.dior",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dior-spaced-initial",
            name="C. Dior Sauvage edt 100ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.detected_brand_text, "c.dior")
        self.assertEqual(parsed.product_name_text, "sauvage")

    def test_typo_abbreviated_brand_aliases_can_be_taught(self):
        brand = Brand.objects.create(name="Salvatore Ferragamo")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="s. ferregamo",
            normalized_alias="s. ferregamo",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="ferragamo-typo",
            name="S. Ferregamo Uomo edt 100ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "uomo")

    def test_specific_brand_alias_beats_supplier_parent_brand_prefix(self):
        Brand.objects.create(name="Hugo Boss")
        brand = Brand.objects.create(name="Baldessarini")
        brand.perfumes.create(
            name="Ambre",
            concentration="Eau de Toilette",
            audience="Men",
        )
        BrandAlias.objects.create(
            brand=brand,
            alias_text="boss baldessarini",
            normalized_alias="boss baldessarini",
            priority=10,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="boss-baldessarini-ambre-tester",
            name="BOSS BALDESSARINI AMBRE men edt 90 ml TESTER",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.detected_brand_text, "boss baldessarini")
        self.assertEqual(parsed.product_name_text, "Ambre")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("90.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertTrue(parsed.is_tester)
        self.assertEqual(
            parsed.display_identity,
            "Baldessarini / Ambre / Eau de Toilette / 90ml / Tester",
        )

    def test_generated_brand_alias_does_not_use_generic_collection_suffix(self):
        attar = Brand.objects.create(name="Attar Collection")
        clive = Brand.objects.create(name="Clive Christian")
        BrandAlias.objects.create(
            brand=clive,
            alias_text="Clive Christian",
            normalized_alias="clive christian",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="collection-not-attar",
            name="Original Collection 1872 FEMININE Perfume Spray 50 ml",
        )

        parsed = parse_supplier_product(product)

        self.assertNotEqual(parsed.normalized_brand, attar)
        self.assertIsNone(parsed.normalized_brand)
        self.assertIn("brand missing", parsed.warnings)

    def test_brand_scoped_collection_alias_can_infer_missing_brand(self):
        Brand.objects.create(name="Attar Collection")
        clive = Brand.objects.create(name="Clive Christian")
        ProductAlias.objects.create(
            brand=clive,
            alias_text="original collection",
            canonical_text="",
            collection_name="Original Collection",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="collection-alias-inferred-brand",
            name="Original Collection 1872 FEMININE Perfume Spray 50 ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, clive)
        self.assertEqual(parsed.collection_name, "Original Collection")
        self.assertIn("1872", parsed.product_name_text)
        self.assertNotIn("brand missing", parsed.warnings)

    def test_attar_prefix_is_attar_collection_brand_not_oil_concentration(self):
        attar_collection = Brand.objects.create(name="Attar Collection")
        Brand.objects.create(name="Al Attar")
        BrandAlias.objects.create(
            brand=attar_collection,
            alias_text="Attar",
            normalized_alias="attar",
            priority=35,
        )
        attar_collection.perfumes.create(
            name="Azalea",
            concentration="Eau de Parfum",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="attar-azalea-edp-not-oil",
            name="ATTAR AZALEA 100ml edP test",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, attar_collection)
        self.assertEqual(parsed.product_name_text, "Azalea")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertTrue(parsed.is_tester)

    def test_exact_brand_still_beats_generic_collection_suffix(self):
        Brand.objects.create(name="Attar Collection")
        regalien = Brand.objects.create(name="Regalien")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="regalien-heritage-not-attar",
            name="Regalien Heritage Collection Sah Extrait De Parfum 50ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, regalien)
        self.assertEqual(parsed.product_name_text, "heritage collection sah")
        self.assertEqual(parsed.concentration, "Extrait de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))

    def test_standalone_w_and_m_are_audience_aliases_not_product_name(self):
        brand = Brand.objects.create(name="Abercrombie & Fitch")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Abercrombie Fitch",
            normalized_alias="abercrombie fitch",
        )
        chanel = Brand.objects.create(name="Chanel")
        BrandAlias.objects.create(
            brand=chanel, alias_text="Chanel", normalized_alias="chanel"
        )
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
        fem_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="audience-fem",
            name="CHANEL COCO fem edp 50ml",
        )

        woman_parse = parse_supplier_product(woman_product)
        men_parse = parse_supplier_product(men_product)
        fem_parse = parse_supplier_product(fem_product)

        self.assertEqual(woman_parse.supplier_gender_hint, "Woman")
        self.assertEqual(woman_parse.product_name_text, "authentic moment")
        self.assertTrue(woman_parse.is_tester)
        self.assertEqual(men_parse.supplier_gender_hint, "Men")
        self.assertEqual(men_parse.product_name_text, "authentic")
        self.assertEqual(fem_parse.supplier_gender_hint, "Woman")
        self.assertEqual(fem_parse.product_name_text, "coco")

    def test_parenthetical_l_is_exact_woman_marker_not_product_name(self):
        brand = Brand.objects.create(name="Kenzo")
        BrandAlias.objects.create(
            brand=brand, alias_text="Kenzo", normalized_alias="kenzo"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="kenzo-ciel-l",
            name="Kenzo Ciel Magnolia (L) 75 ml EDP TECTEP",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(parsed.product_name_text, "ciel magnolia")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("75.00"))
        self.assertTrue(parsed.is_tester)

    def test_bracketed_l_is_exact_woman_marker_not_product_name(self):
        armand_basi = Brand.objects.create(name="Armand Basi")
        BrandAlias.objects.create(
            brand=armand_basi,
            alias_text="Armand Basi",
            normalized_alias="armand basi",
        )
        armaf = Brand.objects.create(name="Armaf")
        BrandAlias.objects.create(
            brand=armaf,
            alias_text="Armaf",
            normalized_alias="armaf",
        )
        examples = (
            (
                "armand-basi-in-red-bracket-l",
                "Armand Basi in Red [ L ] Edt 100ml Tester",
                armand_basi,
                "in red",
                "Eau de Toilette",
                Decimal("100.00"),
                True,
            ),
            (
                "armaf-club-de-nuit-intense-bracket-l",
                "Armaf Club De Nuit Intense [ L] edp 105 ml",
                armaf,
                "club de nuit intense",
                "Eau de Parfum",
                Decimal("105.00"),
                False,
            ),
        )

        for (
            identity_key,
            name,
            expected_brand,
            expected_name,
            expected_concentration,
            expected_size,
            expected_tester,
        ) in examples:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=identity_key,
                    name=name,
                )

                parsed = parse_supplier_product(product)

                self.assertEqual(parsed.normalized_brand, expected_brand)
                self.assertEqual(parsed.supplier_gender_hint, "Woman")
                self.assertEqual(parsed.product_name_text, expected_name)
                self.assertEqual(parsed.concentration, expected_concentration)
                self.assertEqual(parsed.size_ml, expected_size)
                self.assertEqual(parsed.is_tester, expected_tester)
                self.assertNotIn("gender missing", parsed.warnings)

    def test_parenthetical_u_is_unisex_marker_not_product_name(self):
        brand = Brand.objects.create(name="Agonist")
        BrandAlias.objects.create(
            brand=brand, alias_text="Agonist", normalized_alias="agonist"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="agonist-liquid-crystal-u",
            name="AGONIST Liquid Crystal (U) 50ml EDP",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "liquid crystal")
        self.assertEqual(parsed.supplier_gender_hint, "Unisex")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))
        self.assertNotIn("gender missing", parsed.warnings)

    def test_latin_brand_scent_drops_cyrillic_supplier_leftover_tokens(self):
        brand = Brand.objects.create(name="Amouage")
        BrandAlias.objects.create(
            brand=brand, alias_text="AMOUAGE", normalized_alias="amouage"
        )
        brand.perfumes.create(
            name="Ashore", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="amouage-ashore-oman",
            name="AMOUAGE Ashore (L) 100ml EDP TECTEP (ОМАН)",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Ashore")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertTrue(parsed.is_tester)
        self.assertEqual(
            parsed.display_identity, "Amouage / Ashore / Eau de Parfum / 100ml / Tester"
        )

    def test_messy_alians_spacing_tester_and_design_notes_are_structured(self):
        brand = Brand.objects.create(name="Amouage")
        BrandAlias.objects.create(
            brand=brand, alias_text="AMOUAGE", normalized_alias="amouage"
        )
        brand.perfumes.create(
            name="Blossom Love", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="amouage-blossom-love-messy",
            name="AMOUAGE Blossom Love (L)100ml EDP TECTEPс фир.крыш.(нов. ди",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Blossom Love")
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertTrue(parsed.is_tester)
        self.assertEqual(parsed.variant_type, "tester")
        self.assertEqual(parsed.packaging, "new_design with_cap")
        self.assertNotIn("size ambiguous", parsed.warnings)
        self.assertNotIn("gender missing", parsed.warnings)

    def test_messy_alians_decoded_old_design_and_cap_notes_are_structured(self):
        brand = Brand.objects.create(name="Amouage")
        BrandAlias.objects.create(
            brand=brand, alias_text="AMOUAGE", normalized_alias="amouage"
        )
        brand.perfumes.create(
            name="Epic", concentration="Eau de Parfum", audience="Men"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="amouage-epic-decoded-old-design",
            name="AMOUAGE Epic (M) 100ml EDP TECTEP (dec.c фирм.крышкой) ст.д",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Epic")
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertTrue(parsed.is_tester)
        self.assertEqual(parsed.variant_type, "decoded")
        self.assertEqual(parsed.display_variant_type, "Decoded")
        self.assertEqual(parsed.packaging, "old_design with_cap")

    def test_messy_alians_no_box_and_dented_notes_are_structured(self):
        brand = Brand.objects.create(name="Amouage")
        BrandAlias.objects.create(
            brand=brand, alias_text="AMOUAGE", normalized_alias="amouage"
        )
        brand.perfumes.create(
            name="Dia", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="amouage-dia-sample-no-box-dented",
            name="AMOUAGE Dia (L) 7.5ml EDP ПРОБНИК Б/К подмятая",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Dia")
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(parsed.size_ml, Decimal("7.50"))
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertTrue(parsed.is_sample)
        self.assertEqual(parsed.variant_type, "sample")
        self.assertEqual(parsed.packaging, "dented no_box")

    def test_masculine_dented_packaging_note_is_structured(self):
        brand = Brand.objects.create(name="Afnan")
        BrandAlias.objects.create(
            brand=brand, alias_text="Afnan", normalized_alias="afnan"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="afnan-tribute-blue-dented",
            name="Afnan Tribute Blue Exlusive (M) 100ml EDP подмятый",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "tribute blue exlusive")
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.packaging, "dented")
        self.assertNotIn("подмятый", parsed.product_name_text.lower())
        self.assertEqual(
            parsed.display_identity,
            "Afnan / Tribute Blue Exlusive / Eau de Parfum / 100ml / Dented",
        )

    def test_no_cellophane_and_damaged_wrap_terms_are_dented_packaging(self):
        brand = Brand.objects.create(name="Armani")
        BrandAlias.objects.create(
            brand=brand, alias_text="Armani", normalized_alias="armani"
        )
        brand.perfumes.create(
            name="Code", concentration="Eau de Parfum", audience="Men"
        )
        terms = (
            "\u0431\u0435\u0437 \u0446\u0435\u043b",
            "\u0431\u0435\u0437 \u0446\u0435\u043b\u043e\u0444\u043d\u0430",
            "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d\u0440 \u0441\u043b\u044e\u0434\u0430",
        )
        for term in terms:
            GlobalRule.objects.update_or_create(
                rule_kind="parser_dented_packaging_term",
                scope_type="global",
                rule_text=term,
                defaults={
                    "title": f"Dented packaging term: {term}",
                    "priority": 40,
                    "approved": True,
                    "active": True,
                },
            )
        cache.clear()

        for index, term in enumerate(terms, start=1):
            with self.subTest(term=term):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=f"armani-code-dented-wrap-{index}",
                    name=f"Armani Code (M) 15ml EDP ({term})",
                )

                parsed = parse_supplier_product(product)

                self.assertEqual(parsed.normalized_brand, brand)
                self.assertEqual(parsed.product_name_text, "Code")
                self.assertEqual(parsed.supplier_gender_hint, "Men")
                self.assertEqual(parsed.concentration, "Eau de Parfum")
                self.assertEqual(parsed.size_ml, Decimal("15.00"))
                self.assertEqual(parsed.packaging, "dented")
                self.assertNotIn(term, parsed.product_name_text.lower())
                self.assertEqual(
                    parsed.display_identity,
                    "Armani / Code / Eau de Parfum / 15ml / Dented",
                )

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
        self.assertEqual(
            parsed.display_identity,
            "Byredo / Rose of No Man's Land in Bloom / Eau de Parfum / 100ml",
        )

        parsed.product_name_text = "for her"
        self.assertEqual(parsed.display_product_name, "for Her")
        parsed.product_name_text = "The Majestic Amber"
        self.assertEqual(parsed.display_product_name, "The Majestic Amber")

    def test_self_titled_catalogue_scent_fills_brand_only_supplier_row_and_collapses_display(
        self,
    ):
        brand = Brand.objects.create(name="Agent Provocateur")
        brand.perfumes.create(
            name="Agent Provocateur",
            concentration="Eau de Parfum",
            audience="Woman",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="agent-provocateur-self-titled",
            name="Agent Provocateur (L) 50ml EDP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Agent Provocateur")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertNotIn("product name missing", parsed.warnings)
        self.assertEqual(
            parsed.display_identity, "Agent Provocateur / Eau de Parfum / 50ml"
        )

    def test_blank_catalogue_scent_can_confirm_self_titled_brand_only_supplier_row(
        self,
    ):
        brand = Brand.objects.create(name="Example Self Title")
        brand.perfumes.create(
            name="",
            concentration="Eau de Parfum",
            audience="Woman",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="blank-catalog-self-title",
            name="Example Self Title (L) 50ml EDP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Example Self Title")
        self.assertEqual(
            parsed.display_identity, "Example Self Title / Eau de Parfum / 50ml"
        )

    def test_brand_alias_left_as_name_can_confirm_self_titled_catalogue_scent(self):
        brand = Brand.objects.create(name="Salvador Dali")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="SD",
            normalized_alias="sd",
            priority=25,
        )
        brand.perfumes.create(
            name="Salvador Dali",
            concentration="Eau de Parfum",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="salvador-dali-self-titled",
            name="SD Salvador Dali edp 30 ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Salvador Dali")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("30.00"))
        self.assertNotIn("product name missing", parsed.warnings)
        self.assertEqual(
            parsed.display_identity, "Salvador Dali / Eau de Parfum / 30ml"
        )

    def test_ambiguous_self_titled_catalogue_candidates_go_to_manual_review(self):
        brand = Brand.objects.create(name="Example Ambiguous")
        brand.perfumes.create(name="", concentration="Eau de Parfum", audience="Woman")
        brand.perfumes.create(
            name="Example Ambiguous", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="ambiguous-self-title",
            name="Example Ambiguous (L) 50ml EDP",
        )

        parsed = save_parse(product, force=True)

        self.assertFalse(parsed.product_name_text)
        self.assertIn(MANUAL_REVIEW_MODIFIER, parsed.modifiers)
        self.assertIn("self-titled catalogue match ambiguous", parsed.warnings)
        self.assertIn("product name missing", parsed.warnings)

    def test_femme_keeps_supplier_style_but_matches_women_group(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="audience-femme",
            name="DG Light Blue pour femme edt 100ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.supplier_gender_hint, "Pour Femme")
        self.assertEqual(parsed.product_name_text, "light blue pour femme")

    def test_for_woman_suffix_canonicalizes_to_catalogue_scent_name(self):
        brand = Brand.objects.create(name="Carolina Herrera")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Carolina Herrera",
            normalized_alias="carolina herrera",
        )
        brand.perfumes.create(
            name="212 Woman", concentration="Eau de Toilette", audience="Woman"
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="Woman",
            canonical_text="Woman",
            concentration="Eau de Toilette",
            priority=50,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="carolina-herrera-212-woman",
            name="Carolina Herrera 212 for Woman Eau de Toilette 30ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(parsed.product_name_text, "212 Woman")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("30.00"))
        self.assertEqual(
            parsed.display_identity,
            "Carolina Herrera / 212 Woman / Eau de Toilette / 30ml",
        )

    def test_wom_audience_alias_canonicalizes_to_catalogue_audience_scent(self):
        brand = Brand.objects.create(name="Gucci")
        brand.perfumes.create(
            name="Guilty",
            concentration="Eau de Toilette",
        )
        brand.perfumes.create(
            name="Guilty Pour Femme",
            concentration="Eau de Toilette",
            audience="Women",
        )
        brand.perfumes.create(
            name="Guilty Pour Homme",
            concentration="Eau de Toilette",
            audience="Men",
        )
        GlobalRule.objects.create(
            title="Audience alias: wom",
            rule_kind="parser_audience_term",
            scope_type="global",
            rule_text="wom => Woman | women",
            approved=True,
            active=True,
        )
        cache.clear()
        woman_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="gucci-guilty-wom",
            name="GUCCI GUILTY wom edt 90 ml",
        )
        men_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="gucci-guilty-men",
            name="GUCCI GUILTY men edt 90 ml",
        )

        woman_parse = parse_supplier_product(woman_product)
        men_parse = parse_supplier_product(men_product)

        self.assertEqual(woman_parse.normalized_brand, brand)
        self.assertEqual(woman_parse.supplier_gender_hint, "Woman")
        self.assertEqual(woman_parse.product_name_text, "Guilty Pour Femme")
        self.assertNotIn("gender missing", woman_parse.warnings)
        self.assertEqual(woman_parse.confidence, 100)
        self.assertEqual(men_parse.product_name_text, "Guilty Pour Homme")
        self.assertEqual(men_parse.supplier_gender_hint, "Men")

    def test_same_catalogue_name_with_multiple_audiences_gets_name_suffix(self):
        brand = Brand.objects.create(name="Gucci")
        brand.perfumes.create(
            name="Guilty",
            concentration="Eau de Toilette",
            audience="Women",
        )
        brand.perfumes.create(
            name="Guilty",
            concentration="Eau de Toilette",
            audience="Men",
        )
        GlobalRule.objects.create(
            title="Audience alias: wom",
            rule_kind="parser_audience_term",
            scope_type="global",
            rule_text="wom => Woman | women",
            approved=True,
            active=True,
        )
        cache.clear()
        woman_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="gucci-guilty-generic-wom",
            name="GUCCI GUILTY wom edt 90 ml",
        )
        men_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="gucci-guilty-generic-men",
            name="GUCCI GUILTY men edt 90 ml",
        )

        self.assertEqual(
            parse_supplier_product(woman_product).product_name_text,
            "Guilty Woman",
        )
        self.assertEqual(
            parse_supplier_product(men_product).product_name_text,
            "Guilty Man",
        )

    def test_mixed_script_cyrillic_lookalike_keeps_latin_scent_name_and_audience_catalogue_match(
        self,
    ):
        brand = Brand.objects.create(name="Amouage")
        BrandAlias.objects.create(
            brand=brand, alias_text="AMOUAGE", normalized_alias="amouage"
        )
        brand.perfumes.create(
            name="Ciel for Men", concentration="Eau de Parfum", audience="Men"
        )
        brand.perfumes.create(
            name="Ciel for Woman", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="amouage-ciel-cyrillic-c",
            name="AMOUAGE \u0421iel (L) 50ml EDP (\u041e\u041c\u0410\u041d)",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Ciel for Woman")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity, "Amouage / Ciel for Woman / Eau de Parfum / 50ml"
        )

    def test_supplier_packaging_abbreviations_do_not_leave_c_in_reflection_name(self):
        brand = Brand.objects.create(name="Amouage")
        BrandAlias.objects.create(
            brand=brand, alias_text="AMOUAGE", normalized_alias="amouage"
        )
        brand.perfumes.create(
            name="Reflection for Men", concentration="Eau de Parfum", audience="Men"
        )
        brand.perfumes.create(
            name="Reflection for Woman", concentration="Eau de Parfum", audience="Woman"
        )
        GlobalRule.objects.update_or_create(
            rule_kind="parser_with_cap_packaging_term",
            scope_type="global",
            rule_text="c \u0444\u0438\u0440\u043c. \u043a\u0440\u044b\u0448",
            defaults={
                "title": "With-cap packaging term: c firm krysh",
                "priority": 45,
                "approved": True,
                "active": True,
            },
        )
        GlobalRule.objects.update_or_create(
            rule_kind="parser_old_design_packaging_term",
            scope_type="global",
            rule_text="\u0441\u0442.\u0434\u0438",
            defaults={
                "title": "Old-design packaging term: st.di",
                "priority": 45,
                "approved": True,
                "active": True,
            },
        )
        cache.clear()
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="amouage-reflection-men-cap-old-design",
            name="AMOUAGE Reflection (M) 100ml EDP TECTEP(c \u0444\u0438\u0440\u043c. \u043a\u0440\u044b\u0448) \u0441\u0442.\u0434\u0438",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Reflection for Men")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertTrue(parsed.is_tester)
        self.assertEqual(parsed.packaging, "old_design with_cap")
        self.assertNotIn(" c", parsed.product_name_text.lower())
        self.assertEqual(
            parsed.display_identity,
            "Amouage / Reflection for Men / Eau de Parfum / 100ml / Tester / Old Design With Cap",
        )

    def test_short_with_cap_abbreviation_does_not_leave_c_in_guidance_name(self):
        brand = Brand.objects.create(name="Amouage")
        BrandAlias.objects.create(
            brand=brand, alias_text="AMOUAGE", normalized_alias="amouage"
        )
        brand.perfumes.create(
            name="Guidance 46", concentration="Extrait de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="amouage-guidance-46-short-cap-decoded",
            name="AMOUAGE Guidance 46 (L) 100ml EDP TECTEP( c \u0444\u0438\u0440\u043c. \u043a\u0440) dec",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Guidance 46")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertTrue(parsed.is_tester)
        self.assertEqual(parsed.variant_type, "decoded")
        self.assertEqual(parsed.packaging, "with_cap")
        self.assertEqual(
            parsed.display_identity,
            "Amouage / Guidance 46 / Eau de Parfum / 100ml / Decoded / With Cap",
        )

    def test_limited_ed_abbreviation_uses_catalogue_audience_name_before_edition_suffix(
        self,
    ):
        brand = Brand.objects.create(name="Amouage")
        BrandAlias.objects.create(
            brand=brand, alias_text="AMOUAGE", normalized_alias="amouage"
        )
        brand.perfumes.create(
            name="Reflection for Men", concentration="Eau de Parfum", audience="Men"
        )
        brand.perfumes.create(
            name="Reflection for Woman", concentration="Eau de Parfum", audience="Woman"
        )
        GlobalRule.objects.update_or_create(
            rule_kind="regex_preprocess",
            scope_type="global",
            rule_text=r"\blimited\s+ed\.?\b => limited edition",
            defaults={
                "title": "Normalize Limited Ed supplier abbreviation",
                "priority": 20,
                "approved": True,
                "active": True,
            },
        )
        cache.clear()
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="amouage-reflection-men-limited-ed",
            name="AMOUAGE Reflection (M) 100ml EDP Limited Ed.(\u0441\u0442.\u0434\u0438\u0437\u0430\u0439\u043d)",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Reflection for Men Limited Edition")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertEqual(parsed.packaging, "old_design")
        self.assertEqual(
            parsed.display_identity,
            "Amouage / Reflection for Men Limited Edition / Eau de Parfum / 100ml / Old Design",
        )

    def test_francais_brand_alias_is_not_kept_in_12_parfumeurs_scent_name(self):
        brand = Brand.objects.create(name="12 Parfumeurs")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="12 parfumeurs francais",
            normalized_alias="12 parfumeurs francais",
            priority=20,
        )
        brand.perfumes.create(name="Azay-Le-Rideau", concentration="Extrait de Parfum")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="12-parfumeurs-azay",
            name="12 PARFUMEURS FRANCAIS AZAY- LE- RIDEAU 100ml parfume",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.detected_brand_text, "12 parfumeurs francais")
        self.assertEqual(parsed.product_name_text, "Azay-Le-Rideau")
        self.assertEqual(parsed.concentration, "Extrait de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(
            parsed.display_identity,
            "12 Parfumeurs / Azay-Le-Rideau / Extrait de Parfum / 100ml",
        )

    def test_exclus_edition_typo_normalizes_to_catalogue_exclusive_edition(self):
        brand = Brand.objects.create(name="Armani")
        BrandAlias.objects.create(
            brand=brand, alias_text="Armani", normalized_alias="armani"
        )
        brand.perfumes.create(
            name="My Way Exclusive Edition", concentration="Eau de Parfum"
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="My Way Exclusive Edition",
            canonical_text="My Way Exclusive Edition",
            concentration="Eau de Parfum",
            priority=50,
            active=True,
        )
        GlobalRule.objects.update_or_create(
            rule_kind="regex_preprocess",
            scope_type="global",
            rule_text=r"\bexclus\s+edition\b => exclusive edition",
            defaults={
                "title": "Normalize Exclus Edition supplier typo",
                "priority": 20,
                "approved": True,
                "active": True,
            },
        )
        cache.clear()
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="armani-my-way-exclus-edition",
            name="Armani My Way (L) 50ml EDP  Exclus Edition",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "My Way Exclusive Edition")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity,
            "Armani / My Way Exclusive Edition / Eau de Parfum / 50ml",
        )

    def test_name_bearing_audience_alias_preserves_preceding_scent_words(self):
        brand = Brand.objects.create(name="Versace")
        BrandAlias.objects.create(
            brand=brand, alias_text="VERSACE", normalized_alias="versace"
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="pour femme",
            canonical_text="Pour Femme",
            audience="Pour Femme",
            priority=20,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="versace-eros-pour-femme",
            name="VERSACE EROS pour femme edt 100 ml 2015",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "eros Pour Femme")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.release_year, 2015)
        self.assertEqual(parsed.supplier_gender_hint, "Pour Femme")

    def test_release_year_is_stored_separately_not_displayed_in_name(self):
        brand = Brand.objects.create(name="Versace")
        BrandAlias.objects.create(
            brand=brand, alias_text="VERSACE", normalized_alias="versace"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="versace-eros-flame-year",
            name="VERSACE EROS Flame Man edp 100 ml 2019 Tester",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "eros flame man")
        self.assertEqual(parsed.release_year, 2019)
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(
            parsed.display_identity,
            "Versace / Eros Flame Man / Eau de Parfum / 100ml / Tester",
        )

    def test_four_digit_number_between_scent_words_stays_in_name(self):
        brand = Brand.objects.create(name="Norana Perfumes")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Noran Perfumes",
            normalized_alias="noran perfumes",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="noran-moon-1947-white",
            name="Noran Perfumes Moon 1947 White edp 100 ml Tester",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "moon 1947 white")
        self.assertIsNone(parsed.release_year)
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertTrue(parsed.is_tester)

    def test_starting_brand_alias_beats_later_brand_name_in_scent(self):
        montblanc = Brand.objects.create(name="Montblanc")
        signature = Brand.objects.create(name="Signature")
        BrandAlias.objects.create(
            brand=montblanc,
            alias_text="MONT BLANC",
            normalized_alias="mont blanc",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=montblanc,
            alias_text="signature",
            canonical_text="",
            collection_name="Signature",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="montblanc-signature-absolue",
            name="MONT BLANC Signature Absolue edp 90 ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, montblanc)
        self.assertNotEqual(parsed.normalized_brand, signature)
        self.assertEqual(parsed.collection_name, "Signature")
        self.assertEqual(parsed.product_name_text, "absolue")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("90.00"))

    def test_collection_alias_does_not_blank_base_scent_name(self):
        brand = Brand.objects.create(name="Montblanc")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="MONT BLANC",
            normalized_alias="mont blanc",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="signature",
            canonical_text="",
            collection_name="Signature",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="montblanc-signature",
            name="MONT BLANC Signature edp 90 ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "Signature")
        self.assertEqual(parsed.product_name_text, "signature")

    def test_dunhill_signature_collection_parses_brand_collection_and_scent(self):
        dunhill = Brand.objects.create(name="Alfred Dunhill")
        signature = Brand.objects.create(name="Signature")
        BrandAlias.objects.create(
            brand=dunhill,
            alias_text="A.DUNHILL",
            normalized_alias="a.dunhill",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=dunhill,
            alias_text="signature collection",
            canonical_text="",
            collection_name="Signature Collection",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dunhill-signature-collection-arabian-desert",
            name="A.DUNHILL SIGNATURE COLLECTION ARABIAN DESERT 100ml edP TEST",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, dunhill)
        self.assertNotEqual(parsed.normalized_brand, signature)
        self.assertEqual(parsed.collection_name, "Signature Collection")
        self.assertEqual(parsed.product_name_text, "arabian desert")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertTrue(parsed.is_tester)

    def test_dunhill_desire_red_alias_treats_red_as_supplier_noise(self):
        dunhill = Brand.objects.create(name="Alfred Dunhill")
        BrandAlias.objects.create(
            brand=dunhill,
            alias_text="Alfred Dunhill",
            normalized_alias="alfred dunhill",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=dunhill,
            alias_text="desire red",
            canonical_text="Desire for Men",
            audience="Men",
            priority=25,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dunhill-desire-red",
            name="Alfred Dunhill Desire RED (M) 100ml edt",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, dunhill)
        self.assertEqual(parsed.product_name_text, "Desire for Men")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertEqual(
            parsed.display_identity,
            "Alfred Dunhill / Desire for Men / Eau de Toilette / 100ml",
        )

    def test_alexandre_j_art_deco_collection_prefix_parses_collection_and_scent(self):
        brand = Brand.objects.create(name="Alexandre J.")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="ALEXANDRE. J",
            normalized_alias="alexandre. j",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="art deco",
            canonical_text="",
            collection_name="The Art Deco Collector",
            priority=30,
        )
        brand.perfumes.create(
            name="The Majestic Amber",
            collection_name="The Art Deco Collector",
            concentration="Eau de Parfum",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="alexandre-j-art-deco-majestic-amber",
            name="ALEXANDRE. J Art Deco The Majestic Amber 100мл EDP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "The Art Deco Collector")
        self.assertEqual(parsed.product_name_text, "The Majestic Amber")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(
            parsed.display_identity,
            "Alexandre J. / The Art Deco Collector / The Majestic Amber / Eau de Parfum / 100ml",
        )

    def test_alexandre_j_legacy_wb_supplier_ultimate_prefix_is_not_collection(self):
        brand = Brand.objects.create(name="Alexandre J.")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="ALEXANDRE. J",
            normalized_alias="alexandre. j",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="ultimate legacy wb",
            canonical_text="Legacy WB",
            priority=25,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="legacy wb",
            canonical_text="Legacy WB",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="alexandre-j-legacy-wb",
            name="ALEXANDRE. J Ultimate Legacy WB (L) 100\u043c\u043b EDP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "")
        self.assertEqual(parsed.product_name_text, "Legacy WB")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity, "Alexandre J. / Legacy WB / Eau de Parfum / 100ml"
        )

    def test_alexandre_j_ultimate_crystal_saint_honore_uses_ultimate_collection_and_st_name(
        self,
    ):
        brand = Brand.objects.create(name="Alexandre J.")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="ALEXANDRE. J",
            normalized_alias="alexandre. j",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="ultimate crystal saint honore",
            canonical_text="St Honore",
            collection_name="Ultimate Collection",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="ultimate crystal",
            canonical_text="",
            collection_name="Ultimate Collection",
            priority=25,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="saint honore",
            canonical_text="St Honore",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="alexandre-j-ultimate-crystal-saint-honore",
            name="ALEXANDRE. J Ultimate Crystal Saint Honore (L) 45\u043c\u043b EDP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "Ultimate Collection")
        self.assertEqual(parsed.product_name_text, "St Honore")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("45.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity,
            "Alexandre J. / Ultimate Collection / St Honore / Eau de Parfum / 45ml",
        )

    def test_collection_prefix_alias_behavior_is_global(self):
        brand = Brand.objects.create(name="Example House")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Example House",
            normalized_alias="example house",
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="private collection",
            canonical_text="",
            collection_name="Private Collection",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="example-private-collection-oud",
            name="Example House Private Collection Oud edp 50 ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "Private Collection")
        self.assertEqual(parsed.product_name_text, "oud")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))

    def test_woodbox_is_packaging_not_scent_name(self):
        brand = Brand.objects.create(name="Afnan")
        BrandAlias.objects.create(
            brand=brand, alias_text="AFNAN", normalized_alias="afnan"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="afnan-tribute-blue-woodbox",
            name="AFNAN TRIBUTE BLUE WOODBOX 100ml edP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "tribute blue")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.variant_type, "standard")
        self.assertEqual(parsed.packaging, "woodbox")
        self.assertEqual(
            parsed.display_identity,
            "Afnan / Tribute Blue / Eau de Parfum / 100ml / Woodbox",
        )

    def test_new_design_and_gray_box_are_packaging_not_scent_name(self):
        brand = Brand.objects.create(name="Ajmal")
        BrandAlias.objects.create(
            brand=brand, alias_text="AJMAL", normalized_alias="ajmal"
        )
        perfume = brand.perfumes.create(
            name="Shadow", concentration="Eau de Parfum", audience="Men"
        )
        perfume.variants.create(
            size_ml=Decimal("75.00"), variant_type="standard", packaging="gray box"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="ajmal-shadow-gray-box",
            name="AJMAL SHADOW (M) 75ml EDP NEW DESIGN (серый)",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Shadow")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("75.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertEqual(parsed.variant_type, "standard")
        self.assertEqual(parsed.packaging, "gray_box")
        self.assertEqual(
            parsed.display_identity, "Ajmal / Shadow / Eau de Parfum / 75ml / Gray Box"
        )

    def test_product_name_compacts_spaces_around_numeric_dot(self):
        brand = Brand.objects.create(name="Zarkoperfume")
        BrandAlias.objects.create(
            brand=brand, alias_text="Zarkoperfume", normalized_alias="zarkoperfume"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="zarkoperfume-pink-molecule-090-09",
            name="Zarkoperfume PINK MOLeCULE 090 . 09 edp 100 ml Tester",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "pink molecule 090.09")
        self.assertEqual(
            parsed.display_identity,
            "Zarkoperfume / Pink Molecule 090.09 / Eau de Parfum / 100ml / Tester",
        )

    def test_product_name_uses_brand_catalog_spacing_when_compact_name_matches(self):
        brand = Brand.objects.create(name="Paco Rabanne")
        BrandAlias.objects.create(
            brand=brand, alias_text="PACO RABANNE", normalized_alias="paco rabanne"
        )
        brand.perfumes.create(
            name="1 Million", concentration="Eau de Toilette", audience="Men"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="paco-rabanne-1million-men",
            name="PACO RABANNE 1Million men edt 100 ml Tester",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "1 Million")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertTrue(parsed.is_tester)
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertEqual(
            parsed.display_identity,
            "Paco Rabanne / 1 Million / Eau de Toilette / 100ml / Tester",
        )

    def test_catalog_audience_variant_can_canonicalize_scent_name(self):
        brand = Brand.objects.create(name="Abercrombie & Fitch")
        brand.perfumes.create(
            name="Away Tonight Man", concentration="Eau de Parfum", audience="Men"
        )
        brand.perfumes.create(
            name="Away Tonight Woman", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="abercrombie-away-tonight-lady",
            name="Abercrombie & Fitch AWAY TONIGHT lady 30ml edP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Away Tonight Woman")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("30.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity,
            "Abercrombie & Fitch / Away Tonight Woman / Eau de Parfum / 30ml",
        )

    def test_catalog_for_men_variant_can_canonicalize_scent_name(self):
        brand = Brand.objects.create(name="Angel Schlesser")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Angel Schlesser",
            normalized_alias="angel schlesser",
        )
        brand.perfumes.create(
            name="Essential for Men", concentration="Eau de Toilette", audience="Men"
        )
        brand.perfumes.create(
            name="Essential for Women", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="angel-schlesser-essential-men",
            name="Angel Schlesser Essential (M) 100ml edt",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Essential for Men")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertEqual(
            parsed.display_identity,
            "Angel Schlesser / Essential for Men / Eau de Toilette / 100ml",
        )

    def test_catalog_for_women_variant_can_canonicalize_scent_name(self):
        brand = Brand.objects.create(name="Angel Schlesser")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Angel Schlesser",
            normalized_alias="angel schlesser",
        )
        brand.perfumes.create(
            name="Essential for Men", concentration="Eau de Toilette", audience="Men"
        )
        brand.perfumes.create(
            name="Essential for Women", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="angel-schlesser-essential-women",
            name="Angel Schlesser Essential (L) 50ml edp",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Essential for Women")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity,
            "Angel Schlesser / Essential for Women / Eau de Parfum / 50ml",
        )

    def test_catalog_audience_variant_respects_concentration_context(self):
        brand = Brand.objects.create(name="Abercrombie & Fitch")
        brand.perfumes.create(
            name="Away Tonight Woman", concentration="Eau de Toilette", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="abercrombie-away-tonight-lady-edp",
            name="Abercrombie & Fitch AWAY TONIGHT lady 30ml edP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "away tonight")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.supplier_gender_hint, "Woman")

    def test_catalog_base_name_can_drop_trailing_audience_name(self):
        brand = Brand.objects.create(name="Versace")
        BrandAlias.objects.create(
            brand=brand, alias_text="VERSACE", normalized_alias="versace"
        )
        brand.perfumes.create(
            name="Eros Flame", concentration="Eau de Parfum", audience="Men"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="versace-eros-flame-man",
            name="VERSACE EROS Flame Man edp 100 ml 2019 Tester",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Eros Flame")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertTrue(parsed.is_tester)
        self.assertEqual(
            parsed.display_identity,
            "Versace / Eros Flame / Eau de Parfum / 100ml / Tester",
        )

    def test_catalog_base_name_can_drop_trailing_audience_for_ajmal(self):
        brand = Brand.objects.create(name="Ajmal")
        BrandAlias.objects.create(
            brand=brand, alias_text="AJMAL", normalized_alias="ajmal"
        )
        brand.perfumes.create(
            name="Silver Shade", concentration="Eau de Parfum", audience="Men"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="ajmal-silver-shade-man",
            name="AJMAL SILVER SHADE man 100 ml edP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Silver Shade")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")

    def test_catalog_base_name_keeps_trailing_audience_when_named_sibling_exists(self):
        brand = Brand.objects.create(name="Versace")
        BrandAlias.objects.create(
            brand=brand, alias_text="VERSACE", normalized_alias="versace"
        )
        brand.perfumes.create(
            name="Eros Flame", concentration="Eau de Parfum", audience="Men"
        )
        brand.perfumes.create(
            name="Eros Flame Femme", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="versace-eros-flame-man-with-femme-sibling",
            name="VERSACE EROS Flame Man edp 100 ml 2019 Tester",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "eros flame man")
        self.assertEqual(parsed.display_product_name, "Eros Flame Man")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.supplier_gender_hint, "Men")

    def test_catalog_exact_audience_name_still_wins(self):
        brand = Brand.objects.create(name="Abercrombie & Fitch")
        brand.perfumes.create(
            name="Away Tonight Man", concentration="Eau de Parfum", audience="Men"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="abercrombie-away-tonight-man",
            name="Abercrombie & Fitch AWAY TONIGHT man 30ml edP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Away Tonight Man")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("30.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")

    def test_man_eau_fraiche_is_name_bearing_not_modifier_warning(self):
        brand = Brand.objects.create(name="Versace")
        BrandAlias.objects.create(
            brand=brand, alias_text="VERSACE", normalized_alias="versace"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="versace-man-eau-fraiche",
            name="VERSACE Man eau fraiche edt 30 ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "man eau fraiche")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("30.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertNotIn("fraiche", parsed.modifiers)
        self.assertNotIn("fraiche detected", parsed.warnings)

    def test_pour_homme_stays_in_product_name_while_setting_audience(self):
        brand = Brand.objects.create(name="Issey Miyake")
        BrandAlias.objects.create(
            brand=brand, alias_text="Issey Miyake", normalized_alias="issey miyake"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="issey-pour-homme",
            name="ISSEY MIYAKE L'EAU D'ISSEY POUR HOMME SHADES OF KOLAM 125ML EDT TESTER",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.supplier_gender_hint, "Pour Homme")
        self.assertEqual(
            parsed.product_name_text, "l'eau d'issey pour homme shades of kolam"
        )
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("125.00"))
        self.assertTrue(parsed.is_tester)

    def test_redundant_pour_femme_suffix_drops_when_scent_already_has_her(self):
        brand = Brand.objects.create(name="Zadig & Voltaire")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="Zadig & Voltaire",
            normalized_alias="zadig voltaire",
        )
        brand.perfumes.create(
            name="This Is Her", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="zadig-this-is-her-pour-femme",
            name="Zadig & Voltaire This is her pour femme edp 100 ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "This Is Her")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Pour Femme")
        self.assertEqual(
            parsed.display_identity,
            "Zadig & Voltaire / This Is Her / Eau de Parfum / 100ml",
        )

    def test_brand_scoped_collection_alias_extracts_armand_basi_uniform(self):
        brand = Brand.objects.create(name="Armand Basi")
        BrandAlias.objects.create(
            brand=brand, alias_text="A.Basi", normalized_alias="a.basi"
        )
        brand.perfumes.create(
            name="Don't Look Back",
            collection_name="Uniform",
            concentration="Eau de Toilette",
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="uniform",
            canonical_text="",
            collection_name="Uniform",
            priority=30,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="armand-basi-uniform-dont-look-back",
            name="A.Basi Uniform Don't Look Back (L) 100ml edt",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "Uniform")
        self.assertEqual(parsed.product_name_text, "Don't Look Back")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity,
            "Armand Basi / Uniform / Don't Look Back / Eau de Toilette / 100ml",
        )

    def test_catalog_base_name_can_drop_trailing_supplier_marketing_garbage(self):
        brand = Brand.objects.create(name="Afnan")
        BrandAlias.objects.create(
            brand=brand, alias_text="Afnan", normalized_alias="afnan"
        )
        brand.perfumes.create(
            name="Tribute White", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="afnan-tribute-white-exclusive-new",
            name="Afnan Tribute White Exlusive (L) 100ml EDP TECTEP NEW!!!",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Tribute White")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertTrue(parsed.is_tester)
        self.assertEqual(
            parsed.display_identity,
            "Afnan / Tribute White / Eau de Parfum / 100ml / Tester",
        )

    def test_supplier_white_comment_does_not_replace_audience_catalogue_scent(self):
        brand = Brand.objects.create(name="Hugo Boss")
        BrandAlias.objects.create(
            brand=brand, alias_text="H.BOSS", normalized_alias="h.boss"
        )
        brand.perfumes.create(
            name="Boss Woman", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="hugo-boss-woman-white-comment",
            name="H.BOSS WOMAN edp 90 ml \u0411\u0415\u041b\u042b\u0419",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Boss Woman")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("90.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity,
            "Hugo Boss / Boss Woman / Eau de Parfum / 90ml",
        )

    def test_kb_supplier_comment_term_can_strip_non_packaging_notes(self):
        brand = Brand.objects.create(name="Hugo Boss")
        BrandAlias.objects.create(
            brand=brand, alias_text="H.BOSS", normalized_alias="h.boss"
        )
        brand.perfumes.create(
            name="Boss Woman", concentration="Eau de Parfum", audience="Woman"
        )
        GlobalRule.objects.create(
            title="Supplier comment term: violet",
            rule_kind="parser_supplier_comment_term",
            scope_type="global",
            rule_text="\u0444\u0438\u043e\u043b\u0435\u0442\u043e\u0432\u044b\u0439",
            approved=True,
            active=True,
        )
        cache.clear()
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="hugo-boss-woman-violet-comment",
            name="H.BOSS WOMAN edp 90 ml \u0444\u0438\u043e\u043b\u0435\u0442\u043e\u0432\u044b\u0439",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.product_name_text, "Boss Woman")
        self.assertEqual(parsed.packaging, "")
        self.assertEqual(parsed.variant_type, "standard")

    def test_audience_only_catalogue_scent_ignores_duplicate_audience_marker(self):
        brand = Brand.objects.create(name="Hugo Boss")
        BrandAlias.objects.create(
            brand=brand, alias_text="H.BOSS", normalized_alias="h.boss"
        )
        brand.perfumes.create(
            name="Boss Woman", concentration="Eau de Parfum", audience="Woman"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="hugo-boss-woman-l-tester",
            name="H.Boss Woman (L) \u0422\u0415\u0421\u0422\u0415\u0420 90ml EDP",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.product_name_text, "Boss Woman")
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(parsed.variant_type, "tester")
        self.assertEqual(
            parsed.display_identity,
            "Hugo Boss / Boss Woman / Eau de Parfum / 90ml / Tester",
        )

    def test_catalog_base_name_drops_marketing_garbage_even_when_supplier_gender_differs(
        self,
    ):
        brand = Brand.objects.create(name="Afnan")
        BrandAlias.objects.create(
            brand=brand, alias_text="Afnan", normalized_alias="afnan"
        )
        brand.perfumes.create(
            name="Tribute Blue", concentration="Eau de Parfum", audience="Unisex"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="afnan-tribute-blue-exclusive-dented",
            name="Afnan Tribute Blue Exlusive (M) 100ml EDP подмятый",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Tribute Blue")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertEqual(parsed.packaging, "dented")
        self.assertEqual(
            parsed.display_identity,
            "Afnan / Tribute Blue / Eau de Parfum / 100ml / Dented",
        )

    def test_trailing_new_after_size_is_supplier_status_garbage(self):
        brand = Brand.objects.create(name="Afnan")
        BrandAlias.objects.create(
            brand=brand, alias_text="Afnan", normalized_alias="afnan"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="afnan-rave-carbon-new-after-size",
            name="Afnan Rave Carbon (L) 100ml EDP NEW!!!",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "rave carbon")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity, "Afnan / Rave Carbon / Eau de Parfum / 100ml"
        )

    def test_specific_product_alias_beats_blocked_generic_alias(self):
        brand = Brand.objects.create(name="Thierry Mugler")
        BrandAlias.objects.create(
            brand=brand, alias_text="Thierry Mugler", normalized_alias="thierry mugler"
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="angel",
            canonical_text="Angel",
            excluded_terms="etoile des reves",
            priority=10,
            active=True,
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="angel etoile des reves eau de nuit",
            canonical_text="Angel Etoile des Reves Eau de Nuit",
            audience="Woman",
            priority=20,
            active=True,
        )
        etoile_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="mugler-etoile",
            name="THIERRY MUGLER ANGEL ETOILE DES REVES EAU DE NUIT edp WOMAN 100ml",
        )
        angel_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="mugler-angel",
            name="THIERRY MUGLER ANGEL edp WOMAN 100ml",
        )

        etoile_parse = parse_supplier_product(etoile_product)
        angel_parse = parse_supplier_product(angel_product)

        self.assertEqual(
            etoile_parse.product_name_text, "Angel Etoile des Reves Eau de Nuit"
        )
        self.assertEqual(etoile_parse.concentration, "Eau de Parfum")
        self.assertEqual(etoile_parse.size_ml, Decimal("100.00"))
        self.assertEqual(etoile_parse.supplier_gender_hint, "Woman")
        self.assertEqual(angel_parse.product_name_text, "Angel")

    def test_product_alias_can_extract_collection_and_scent(self):
        armani = Brand.objects.create(name="Armani")
        BrandAlias.objects.create(
            brand=armani,
            alias_text="Giorgio Armani",
            normalized_alias="giorgio armani",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=armani,
            alias_text="emporio armani stronger with amber exclusive edi",
            canonical_text="Amber",
            collection_name="Emporio Armani Stronger With You",
            priority=20,
            active=True,
        )
        Brand.objects.create(name="You")
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="armani-amber",
            name="Giorgio Armani Emporio Armani Stronger With You Amber Exclusive Edi edp 100 ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, armani)
        self.assertEqual(parsed.product_name_text, "Amber")
        self.assertEqual(parsed.collection_name, "Emporio Armani Stronger With You")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(
            parsed.display_identity,
            "Armani / Emporio Armani Stronger with You / Amber / Eau de Parfum / 100ml",
        )

    def test_dunhill_signature_collection_alias_keeps_brand_and_collection_separate(
        self,
    ):
        dunhill = Brand.objects.create(name="Alfred Dunhill")
        BrandAlias.objects.create(
            brand=dunhill,
            alias_text="A.DUNHILL",
            normalized_alias="a.dunhill",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=dunhill,
            alias_text="signature collection",
            canonical_text="",
            collection_name="Signature Collection",
            priority=30,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dunhill-arabian-desert-signature",
            name="A.DUNHILL SIGNATURE COLLECTION ARABIAN DESERT 100ml edP TEST",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, dunhill)
        self.assertEqual(parsed.detected_brand_text, "A.DUNHILL")
        self.assertEqual(parsed.collection_name, "Signature Collection")
        self.assertEqual(parsed.product_name_text, "arabian desert")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertTrue(parsed.is_tester)

    def test_catalog_link_copies_collection_name_to_parse(self):
        armani = Brand.objects.create(name="Armani")
        perfume = armani.perfumes.create(
            name="Amber",
            collection_name="Emporio Armani Stronger With You",
            concentration="Eau de Parfum",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="armani-linked-amber",
            name="Giorgio Armani Emporio Armani Stronger With Amber edp 100 ml",
            catalog_perfume=perfume,
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.product_name_text, "Amber")
        self.assertEqual(parsed.collection_name, "Emporio Armani Stronger With You")

    def test_catalog_identity_infers_collection_without_supplier_collection_text(self):
        brand = Brand.objects.create(name="Van Cleef & Arpels")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="VAN CLEEF & ARPELS",
            normalized_alias="van cleef & arpels",
        )
        brand.perfumes.create(
            name="Orchid Leather",
            concentration="Eau de Parfum",
            collection_name="Collection Extraordinaire",
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="vca-orchid-leather-no-collection",
            name="VAN CLEEF & ARPELS Orchid Leather edp 75ml TESTER",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Orchid Leather")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.collection_name, "Collection Extraordinaire")
        self.assertEqual(parsed.size_ml, Decimal("75.00"))
        self.assertTrue(parsed.is_tester)

    def test_product_alias_can_make_modifier_name_bearing(self):
        armani = Brand.objects.create(name="Armani")
        BrandAlias.objects.create(
            brand=armani,
            alias_text="Giorgio Armani",
            normalized_alias="giorgio armani",
            priority=20,
        )
        ProductAlias.objects.create(
            brand=armani,
            alias_text="acqua di gioia intense",
            canonical_text="Acqua di Gioia Intense",
            audience="Woman",
            priority=20,
            active=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="armani-acqua-intense",
            name="Giorgio Armani Acqua Di Gioia (W) edp 100 ml intense tester",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, armani)
        self.assertEqual(parsed.product_name_text, "Acqua di Gioia Intense")
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertTrue(parsed.is_tester)
        self.assertNotIn("intense", parsed.modifiers)
        self.assertNotIn("intense detected", parsed.warnings)
        self.assertEqual(
            parsed.display_identity,
            "Armani / Acqua di Gioia Intense / Eau de Parfum / 100ml / Tester",
        )

    def test_explicit_edp_wins_over_catalogue_link_concentration(self):
        brand = Brand.objects.create(name="Trussardi")
        BrandAlias.objects.create(
            brand=brand, alias_text="Trussardi", normalized_alias="trussardi"
        )
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
        product = SupplierProduct.objects.create(
            supplier=self.supplier, identity_key="2", name="DG Light Blue EDP 100ml"
        )
        parsed = save_parse(product)
        parsed.locked_by_human = True
        parsed.product_name_text = "Human value"
        parsed.save()

        again = save_parse(product)

        self.assertEqual(again.product_name_text, "Human value")

    def test_product_alias_must_match_whole_phrase(self):
        brand = Brand.objects.create(name="12 Parfumeurs")
        BrandAlias.objects.create(
            brand=brand, alias_text="12 Parfumeurs", normalized_alias="12 parfumeurs"
        )
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
        BrandAlias.objects.create(
            brand=brand, alias_text="Montale", normalized_alias="montale"
        )
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
        BrandAlias.objects.create(
            brand=brand, alias_text="100 BON", normalized_alias="100 bon"
        )
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
            (
                "reversed-ml-cyrillic",
                "1916 Agua De Colonia Limon & Tonca мл 100 тестер",
            ),
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
        for index, raw in enumerate(
            ("eau de perfume", "eau de parfume", "eau de parf"), start=1
        ):
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

    def test_kb_sample_terms_mark_probe_tubes_as_sample(self):
        brand = Brand.objects.create(name="Byredo")
        BrandAlias.objects.create(
            brand=brand, alias_text="Byredo", normalized_alias="byredo"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="sample-probe-tube",
            name="Byredo Blanche \u043f\u0440\u043e\u0431\u0438\u0440\u043a\u0430 2ml",
        )

        parsed = parse_supplier_product(product)

        self.assertTrue(parsed.is_sample)
        self.assertEqual(parsed.variant_type, "sample")
        self.assertEqual(parsed.product_name_text, "blanche")

    def test_refill_terms_add_refill_modifier(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="refill",
            name="Some Brand Scent refill 100ml",
        )

        parsed = parse_supplier_product(product)

        self.assertIn("refill", parsed.modifiers)
        self.assertEqual(parsed.display_variant_type, "Refill")
        self.assertNotIn("refill detected", parsed.warnings)

    def test_refillable_supplier_note_is_packaging_not_refill_type(self):
        brand = Brand.objects.create(name="Armani")
        BrandAlias.objects.create(
            brand=brand, alias_text="Armani", normalized_alias="armani"
        )
        brand.perfumes.create(
            name="Acqua di Gio", concentration="Extrait de Parfum", audience="Men"
        )
        GlobalRule.objects.update_or_create(
            rule_kind="parser_refillable_packaging_term",
            scope_type="global",
            rule_text="refillable",
            defaults={
                "title": "Refillable packaging term: refillable",
                "priority": 50,
                "approved": True,
                "active": True,
            },
        )
        cache.clear()
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="armani-acqua-di-gio-refillable",
            name="Armani Acqua di Gio (M) 75ml PARFUM REFILLABLE",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Acqua di Gio")
        self.assertEqual(parsed.concentration, "Extrait de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("75.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Men")
        self.assertNotIn("refill", parsed.modifiers)
        self.assertEqual(parsed.display_variant_type, "Standard")
        self.assertEqual(parsed.packaging, "refillable")
        self.assertEqual(parsed.display_packaging, "Refillable")
        self.assertNotIn("refillable", parsed.product_name_text.lower())
        self.assertNotIn("refill detected", parsed.warnings)
        self.assertEqual(
            parsed.display_identity,
            "Armani / Acqua di Gio / Extrait de Parfum / 75ml / Refillable",
        )

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
        BrandAlias.objects.create(
            brand=brand, alias_text="Chanel", normalized_alias="chanel"
        )
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

    def test_trailing_star_marks_supplier_row_as_fake_garbage(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="trailing-star-fake",
            name="GUY LAROCHE FIDJI wom 14 ml parfum *",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.modifiers, ["garbage"])
        self.assertEqual(parsed.confidence, 100)
        self.assertIn("excluded garbage keyword: fake marker *", parsed.warnings)
        self.assertFalse(parsed.product_name_text)

    def test_internal_star_in_size_does_not_mark_row_as_garbage(self):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="internal-star-size",
            name="Some Brand Discovery Set 2*100ml",
        )

        parsed = parse_supplier_product(product)

        self.assertNotEqual(parsed.modifiers, ["garbage"])

    def test_worn_russian_keyword_routes_to_garbage(self):
        GlobalRule.objects.create(
            title="Garbage keyword: worn",
            rule_kind="garbage_keyword",
            scope_type="global",
            rule_text="потерт",
            active=True,
            approved=True,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="worn-russian",
            name="GUERLAIN L'HEURE de NUIT edp 125 ml потертая",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.modifiers, ["garbage"])
        self.assertEqual(parsed.confidence, 100)
        self.assertIn("excluded garbage keyword: потерт", parsed.warnings)
        self.assertFalse(parsed.product_name_text)

    def test_inspired_by_russian_keyword_routes_to_garbage(self):
        GlobalRule.objects.create(
            title="Garbage keyword: inspired-by imitation rows",
            rule_kind="garbage_keyword",
            scope_type="global",
            rule_text="по мотивам",
            active=True,
            approved=True,
        )
        brand = Brand.objects.create(name="Creed")
        BrandAlias.objects.create(
            brand=brand, alias_text="Creed", normalized_alias="creed"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="inspired-by-creed",
            name="По мотивам Creed Aventus male edp 30 ml CP 005",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.modifiers, ["garbage"])
        self.assertEqual(parsed.confidence, 100)
        self.assertIn("excluded garbage keyword: по мотивам", parsed.warnings)
        self.assertFalse(parsed.product_name_text)

    def test_custom_concentration_aliases_can_be_managed_in_database(self):
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(
            brand=brand, alias_text="Montale", normalized_alias="montale"
        )
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
        BrandAlias.objects.create(
            brand=brand, alias_text="100 Bon", normalized_alias="100 bon"
        )
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

    def test_duplicate_concentration_aliases_are_not_left_in_product_name(self):
        brand = Brand.objects.create(name="Morph")
        BrandAlias.objects.create(
            brand=brand, alias_text="Morph", normalized_alias="morph"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="morph-duplicate-concentration",
            name="Morph N.8 extrait de parfum 2 мл духи пробник",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "n.8")
        self.assertEqual(parsed.concentration, "Extrait de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("2.00"))
        self.assertTrue(parsed.is_sample)

    def test_repeated_brand_can_be_part_of_scent_name(self):
        brand = Brand.objects.create(name="Fendi")
        BrandAlias.objects.create(
            brand=brand, alias_text="Fendi", normalized_alias="fendi"
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="fendi-fan-di-fendi",
            name="FENDI FAN DI FENDI POUR HOMME ASSOLUTO 100ML EDT TESTER",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "fan di fendi pour homme assoluto")
        self.assertEqual(parsed.concentration, "Eau de Toilette")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Pour Homme")
        self.assertTrue(parsed.is_tester)

    def test_chloe_atelier_alias_sets_collection_and_scent(self):
        brand = Brand.objects.create(name="Chloe")
        BrandAlias.objects.create(
            brand=brand, alias_text="Chloe", normalized_alias="chloe"
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="atelier jasminum sambac",
            canonical_text="Jasminum Sambac",
            collection_name="Atelier des Fleurs",
            priority=20,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="chloe-atelier-jasminum-sambac",
            name="Chloe ATELIER Jasminum Sambac 150ml edp TEST",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "Atelier des Fleurs")
        self.assertEqual(parsed.product_name_text, "Jasminum Sambac")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("150.00"))
        self.assertTrue(parsed.is_tester)

    def test_chloe_atelier_collection_alias_keeps_unknown_scent_name(self):
        brand = Brand.objects.create(name="Chloe")
        BrandAlias.objects.create(
            brand=brand, alias_text="Chloe", normalized_alias="chloe"
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="atelier",
            canonical_text="",
            collection_name="Atelier des Fleurs",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="chloe-atelier-magnolia-alba",
            name="Chloe ATELIER Magnolia Alba 150ml edp TEST",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "Atelier des Fleurs")
        self.assertEqual(parsed.product_name_text, "magnolia alba")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("150.00"))
        self.assertTrue(parsed.is_tester)

    def test_van_cleef_collection_extraordinaire_alias_keeps_scent_name(self):
        brand = Brand.objects.create(name="Van Cleef & Arpels")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="VAN CLEEF & ARPELS",
            normalized_alias="van cleef & arpels",
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="collection extraordinaire",
            canonical_text="",
            collection_name="Collection Extraordinaire",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="vca-collection-extraordinaire-neroli-amara",
            name="VAN CLEEF & ARPELS Collection Extraordinaire Neroli Amara edp 15 ml tester",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.collection_name, "Collection Extraordinaire")
        self.assertEqual(parsed.product_name_text, "neroli amara")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("15.00"))
        self.assertTrue(parsed.is_tester)
        self.assertEqual(
            parsed.display_identity,
            "Van Cleef & Arpels / Collection Extraordinaire / Neroli Amara / Eau de Parfum / 15ml / Tester",
        )

    def test_product_alias_prefix_keeps_remaining_scent_words(self):
        brand = Brand.objects.create(name="4711")
        BrandAlias.objects.create(
            brand=brand, alias_text="4711", normalized_alias="4711"
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="acqua colonia",
            canonical_text="Acqua Colonia",
            priority=40,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="4711-acqua-colonia-pink-pepper-grapefruit",
            name="4711 Acqua Colonia Pink Pepper & Grapefruit tester edc170ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(
            parsed.product_name_text, "Acqua Colonia pink pepper & grapefruit"
        )
        self.assertEqual(parsed.concentration, "Eau de Cologne")
        self.assertEqual(parsed.size_ml, Decimal("170.00"))
        self.assertTrue(parsed.is_tester)

    def test_brand_scoped_for_her_alias_preserves_narciso_scent_name(self):
        brand = Brand.objects.create(name="Narciso Rodriguez")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="NARCISO RODRIGUEZ",
            normalized_alias="narciso rodriguez",
        )
        ProductAlias.objects.create(
            brand=brand,
            alias_text="for her",
            canonical_text="for Her",
            audience="Woman",
            priority=30,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="narciso-for-her-edp-100",
            name="NARCISO RODRIGUEZ for her edp 100 ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "for Her")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.supplier_gender_hint, "Woman")
        self.assertEqual(
            parsed.display_identity,
            "Narciso Rodriguez / for Her / Eau de Parfum / 100ml",
        )

    def test_product_alias_corrects_supplier_scent_misspelling(self):
        brand = Brand.objects.create(name="Ex Nihilo")
        perfume = brand.perfumes.create(
            name="Fleur Narcotique",
            concentration="Eau de Parfum",
        )
        BrandAlias.objects.create(
            brand=brand,
            alias_text="EX NIHILO",
            normalized_alias="ex nihilo",
        )
        ProductAlias.objects.create(
            brand=brand,
            perfume=perfume,
            alias_text="fleur narcotigue",
            canonical_text="Fleur Narcotique",
            priority=25,
        )
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="ex-nihilo-fleur-narcotigue",
            name="EX NIHILO FLEUR NARCOTIGUE edp 50 ml",
        )

        parsed = save_parse(product, force=True)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Fleur Narcotique")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("50.00"))
        self.assertEqual(
            parsed.display_identity,
            "Ex Nihilo / Fleur Narcotique / Eau de Parfum / 50ml",
        )

    def test_xerjoff_casamorati_combination_maps_to_casamorati_brand(self):
        xerjoff = Brand.objects.create(name="Xerjoff")
        casamorati = Brand.objects.create(name="Casamorati")
        BrandAlias.objects.create(
            brand=xerjoff,
            alias_text="xerjoff",
            normalized_alias="xerjoff",
            priority=100,
        )
        BrandAlias.objects.create(
            brand=casamorati,
            alias_text="xerjoff casamorati",
            normalized_alias="xerjoff casamorati",
            priority=10,
        )
        BrandAlias.objects.create(
            brand=casamorati,
            alias_text="casamorati",
            normalized_alias="casamorati",
            priority=10,
        )
        combined_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="xerjoff-casamorati-italica",
            name="Xerjoff Casamorati Italica 100ml edp",
        )
        standalone_product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="xerjoff-naxos",
            name="Xerjoff Naxos 100ml edp",
        )

        combined = parse_supplier_product(combined_product)
        standalone = parse_supplier_product(standalone_product)

        self.assertEqual(combined.normalized_brand, casamorati)
        self.assertEqual(combined.product_name_text, "italica")
        self.assertEqual(combined.concentration, "Eau de Parfum")
        self.assertEqual(combined.size_ml, Decimal("100.00"))
        self.assertEqual(standalone.normalized_brand, xerjoff)
        self.assertEqual(standalone.product_name_text, "naxos")

    def test_supplier_cap_note_is_structured_packaging_not_scent_name(self):
        brand = Brand.objects.create(name="Versace")
        BrandAlias.objects.create(
            brand=brand, alias_text="VERSACE", normalized_alias="versace"
        )
        examples = (
            (
                "versace-yellow-diamond-cap",
                "VERSACE Yellow Diamond edt 90 ml Tester с крышкой",
                "yellow diamond",
            ),
            (
                "versace-bright-crystal-cap",
                "Versace BRIGHT CRYSTAL 90ml edt TEST с крышкой",
                "bright crystal",
            ),
        )

        for identity_key, name, expected_scent in examples:
            with self.subTest(name=name):
                product = SupplierProduct.objects.create(
                    supplier=self.supplier,
                    identity_key=identity_key,
                    name=name,
                )

                parsed = parse_supplier_product(product)

                self.assertEqual(parsed.normalized_brand, brand)
                self.assertEqual(parsed.product_name_text, expected_scent)
                self.assertEqual(parsed.concentration, "Eau de Toilette")
                self.assertEqual(parsed.size_ml, Decimal("90.00"))
                self.assertTrue(parsed.is_tester)
                self.assertEqual(parsed.packaging, "with_cap")
                self.assertEqual(parsed.display_packaging, "With Cap")

    def test_kb_preprocess_normalizes_apostrophe_like_marks_between_letters(self):
        brand = Brand.objects.create(name="State of Mind")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="STATE OF MIND",
            normalized_alias="state of mind",
        )
        GlobalRule.objects.create(
            title="Normalize apostrophe-like marks",
            rule_kind="regex_preprocess",
            scope_type="global",
            rule_text=r"(?<=[\p{L}])\s*[`´‘’ʼʹʽ]\s*(?=[\p{L}]) => '",
            approved=True,
            active=True,
            priority=15,
        )
        cache.clear()
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="state-of-mind-l-ame",
            name="STATE OF MIND L ` Ame Slave edp 100 ml",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "l'ame slave")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))

    def test_kb_preprocess_normalizes_spaced_decimal_dots_between_digits(self):
        brand = Brand.objects.create(name="Zarkoperfume")
        GlobalRule.objects.create(
            title="Normalize spaced decimal dots",
            rule_kind="regex_preprocess",
            scope_type="global",
            rule_text=r"(?<=\d)\s*\.\s*(?=\d) => .",
            approved=True,
            active=True,
            priority=18,
        )
        cache.clear()
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="zarko-pink-molecule-spaced-dot",
            name="Zarkoperfume PINK MOLeCULE 090 . 09 edp 100 ml Tester",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "pink molecule 090.09")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertTrue(parsed.is_tester)

    def test_kb_variant_type_alias_removes_woodbox_from_scent_name(self):
        brand = Brand.objects.create(name="Afnan")
        GlobalRule.objects.create(
            title="Variant type: woodbox",
            rule_kind="parser_variant_type_term",
            scope_type="global",
            rule_text="woodbox => woodbox",
            approved=True,
            active=True,
            priority=40,
        )
        cache.clear()
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="afnan-tribute-blue-woodbox",
            name="AFNAN TRIBUTE BLUE WOODBOX 100ml edP",
        )

        parsed = parse_supplier_product(product)

        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "tribute blue")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertEqual(parsed.variant_type, "woodbox")
        self.assertEqual(parsed.display_variant_type, "Woodbox")

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
    @patch(
        "assistant_linking.services.normalizer.regex.search", side_effect=TimeoutError
    )
    def test_normalizer_skips_alias_on_regex_timeout(
        self, mock_search, mock_mail_admins
    ):
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
