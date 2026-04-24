from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import ParsedSupplierProduct
from catalog.models import Brand
from prices.models import Currency, Supplier, SupplierProduct


User = get_user_model()


class SmartSearchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name="Supplier", code="sup")
        self.brand = Brand.objects.create(name="Brand")

    def create_product(self, name, identity, **parse):
        product = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key=identity,
            name=name,
            currency=Currency.USD,
            current_price=100,
            is_active=True,
        )
        if parse:
            ParsedSupplierProduct.objects.create(
                supplier_product=product,
                raw_name=name,
                normalized_text=name.lower(),
                normalized_brand=self.brand,
                product_name_text=parse.get("product_name_text", name),
                supplier_gender_hint=parse.get("supplier_gender_hint", ""),
                size_ml=parse.get("size_ml"),
                is_tester=parse.get("is_tester", False),
                confidence=90,
            )
        return product

    def search_names(self, query, smart=True):
        response = self.client.get(
            reverse("prices:product_search"),
            {"q": query, "smart": "1" if smart else "0", "status": "active"},
        )
        self.assertEqual(response.status_code, 200)
        return [item["name"] for item in response.json()["items"]]

    def test_men_query_matches_homme_synonym_in_smart_mode(self):
        self.create_product("Classic Homme EDT 100ml", "homme", supplier_gender_hint="men")

        self.assertIn("Classic Homme EDT 100ml", self.search_names("men"))
        self.assertNotIn("Classic Homme EDT 100ml", self.search_names("men", smart=False))

    def test_size_query_excludes_tester_unless_requested(self):
        self.create_product("Blue Talisman EDP 100ml", "classic", product_name_text="Blue Talisman", size_ml="100.00")
        self.create_product("Blue Talisman EDP Tester 100ml", "tester", product_name_text="Blue Talisman", size_ml="100.00", is_tester=True)

        names = self.search_names("blue talisman 100")
        self.assertIn("Blue Talisman EDP 100ml", names)
        self.assertNotIn("Blue Talisman EDP Tester 100ml", names)

        tester_names = self.search_names("blue talisman tester 100")
        self.assertIn("Blue Talisman EDP Tester 100ml", tester_names)

    def test_classic_light_blue_query_excludes_identity_modifiers(self):
        self.create_product("Light Blue Woman EDT 100ml", "classic", product_name_text="Light Blue", supplier_gender_hint="women")
        self.create_product("Light Blue Intense Woman EDP 100ml", "intense", product_name_text="Light Blue Intense", supplier_gender_hint="women")
        self.create_product("Light Blue Love in Capri Woman EDT 100ml", "capri", product_name_text="Light Blue Love in Capri", supplier_gender_hint="women")

        names = self.search_names("light blue woman")

        self.assertIn("Light Blue Woman EDT 100ml", names)
        self.assertNotIn("Light Blue Intense Woman EDP 100ml", names)
        self.assertNotIn("Light Blue Love in Capri Woman EDT 100ml", names)
