from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Brand, Perfume, PerfumeVariant


class OurProductCatalogueListTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(user)
        brand = Brand.objects.create(name="Montale")
        self.perfume = Perfume.objects.create(
            brand=brand,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
            collection_name="Classic",
        )
        self.variant = PerfumeVariant.objects.create(
            perfume=self.perfume,
            size_ml="100.00",
            packaging="box",
            is_tester=True,
        )

    def test_our_products_page_lists_catalogue_variants(self):
        response = self.client.get(reverse("prices:our_product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Montale")
        self.assertContains(response, "Vanilla Extasy")
        self.assertContains(response, "Eau de Parfum")
        self.assertContains(response, "100 ml")
        self.assertContains(response, "tester")
        self.assertContains(response, "box")

    def test_staff_can_inline_edit_catalogue_variant_row(self):
        response = self.client.post(
            reverse("prices:our_product_variant_inline_update", args=[self.variant.pk]),
            {
                "brand_name": "Montale Paris",
                "perfume_name": "Vanilla Extasy Intense",
                "concentration": "Extrait de Parfum",
                "size_ml": "50",
                "is_tester": "0",
                "packaging": "no box",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.brand.name, "Montale Paris")
        self.assertEqual(self.perfume.name, "Vanilla Extasy Intense")
        self.assertEqual(self.perfume.concentration, "Extrait de Parfum")
        self.assertEqual(self.variant.size_ml, 50)
        self.assertFalse(self.variant.is_tester)
        self.assertEqual(self.variant.packaging, "no box")
