from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import BrandAlias, ParsedSupplierProduct, ProductAlias
from assistant_linking.services.normalizer import save_parse
from catalog.models import Brand, Perfume, PerfumeVariant
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
