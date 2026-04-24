from django.test import TestCase

from assistant_linking.models import BrandAlias
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

        self.assertEqual(parsed.concentration, "edt")
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
