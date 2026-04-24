from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import BrandAlias, ParsedSupplierProduct, ProductAlias
from assistant_linking.services.normalizer import save_parse
from catalog.models import Brand
from prices.models import Supplier, SupplierProduct


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
        save_parse(self.product)

    def test_staff_can_teach_parse_inline(self):
        response = self.client.post(
            reverse("assistant_linking:normalization_teach", args=[self.product.id]),
            {
                "brand_name": "Montale",
                "product_name": "Vanilla Extasy",
                "concentration": "edp",
                "size_ml": "100",
                "audience": "",
                "alias_scope": "global",
                "brand_alias_text": "montale",
                "product_alias_text": "vanilla extasy",
                "lock_parse": "on",
                "reparse_similar": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        brand = Brand.objects.get(name="Montale")
        parsed = ParsedSupplierProduct.objects.get(supplier_product=self.product)
        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Vanilla Extasy")
        self.assertEqual(parsed.concentration, "edp")
        self.assertEqual(parsed.size_ml, 100)
        self.assertTrue(parsed.locked_by_human)
        self.assertTrue(BrandAlias.objects.filter(brand=brand, alias_text="montale", supplier__isnull=True).exists())
        self.assertTrue(ProductAlias.objects.filter(brand=brand, alias_text="vanilla extasy", canonical_text="Vanilla Extasy").exists())
