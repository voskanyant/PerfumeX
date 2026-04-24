from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Brand, Perfume, PerfumeVariant


class OurProductCatalogueListTests(TestCase):
    def test_our_products_page_lists_catalogue_perfumes(self):
        user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        brand = Brand.objects.create(name="Montale")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
            collection_name="Classic",
        )
        PerfumeVariant.objects.create(
            perfume=perfume,
            size_ml="100.00",
            packaging="box",
            is_tester=True,
        )

        self.client.force_login(user)
        response = self.client.get(reverse("prices:our_product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Montale")
        self.assertContains(response, "Vanilla Extasy")
        self.assertContains(response, "Eau de Parfum")
        self.assertContains(response, "100 ml")
        self.assertContains(response, "tester")
        self.assertContains(response, "box")
