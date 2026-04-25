from django.test import TestCase

from assistant_linking.models import BrandAlias, ConcentrationAlias, ProductAlias
from assistant_linking.services.normalizer import parse_supplier_product, save_parse
from catalog.models import Brand
from prices.models import Supplier, SupplierProduct


class NormalizerTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Supplier", code="sup")
        self.brand = Brand.objects.create(name="Dolce Gabbana")
        BrandAlias.objects.create(brand=self.brand, alias_text="DG", normalized_alias="dg")

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
        self.assertEqual(parsed.supplier_gender_hint, "men")
        self.assertEqual(parsed.normalized_brand, self.brand)

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
