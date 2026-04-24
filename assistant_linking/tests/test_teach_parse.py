from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import BrandAlias, ParsedSupplierProduct, ProductAlias
from assistant_linking.services.normalizer import save_parse
from assistant_linking.services.teaching import suggest_product_alias_blockers
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
                "concentration": "edp",
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
        self.assertEqual(parsed.concentration, "edp")
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
                "concentration": "edp",
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

    def test_teaching_page_suggests_blockers_from_similar_supplier_rows(self):
        classic = SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="dg-2",
            name="dolce gabanna light blue eau de parfum 100ml",
        )

        blockers = suggest_product_alias_blockers(classic, "light blue", "dolce gabanna")

        self.assertIn("intense", blockers)

    def test_staff_can_accept_catalog_candidate_from_normalization(self):
        brand = Brand.objects.create(name="Montale")
        perfume = Perfume.objects.create(brand=brand, name="Vanilla Extasy", concentration="edp")
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
