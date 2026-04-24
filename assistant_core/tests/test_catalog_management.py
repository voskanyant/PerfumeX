from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from assistant_linking.models import BrandAlias, ProductAlias
from catalog.models import Brand, Perfume, PerfumeVariant


User = get_user_model()


class CatalogManagementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(self.user)
        self.brand = Brand.objects.create(name="Montale")
        self.perfume = Perfume.objects.create(brand=self.brand, name="Vanilla Extasy", concentration="edp")
        self.variant = PerfumeVariant.objects.create(perfume=self.perfume, size_ml="100", variant_type="standard")

    def test_staff_can_search_catalogue_perfumes(self):
        response = self.client.get(reverse("assistant_core:catalog_perfumes"), {"q": "mont"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vanilla Extasy")
        self.assertContains(response, "Montale")

    def test_staff_can_edit_catalogue_brand(self):
        response = self.client.post(
            reverse("assistant_core:catalog_brand_update", args=[self.brand.pk]),
            {
                "name": "Montale Paris",
                "country_of_origin": "France",
                "official_url": "",
                "description": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.name, "Montale Paris")

    def test_staff_can_delete_catalogue_variant(self):
        response = self.client.post(reverse("assistant_core:catalog_variant_delete", args=[self.variant.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PerfumeVariant.objects.filter(pk=self.variant.pk).exists())

    def test_staff_can_merge_catalogue_perfumes(self):
        duplicate = Perfume.objects.create(brand=self.brand, name="Vanilla Extasy", concentration="edp")
        duplicate_variant = PerfumeVariant.objects.create(perfume=duplicate, size_ml="50", variant_type="standard")

        response = self.client.post(
            reverse("assistant_core:catalog_perfume_merge"),
            {"source": duplicate.id, "target": self.perfume.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Perfume.objects.filter(pk=duplicate.pk).exists())
        duplicate_variant.refresh_from_db()
        self.assertEqual(duplicate_variant.perfume, self.perfume)

    def test_staff_can_import_catalogue_from_excel(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["brand", "scent_name", "concentration", "size_ml", "audience", "variant_type", "sku"])
        sheet.append(["Dolce & Gabbana", "Light Blue", "Eau de Toilette", 100, "women", "standard", "DG-LB-100"])
        payload = BytesIO()
        workbook.save(payload)
        payload.seek(0)
        upload = SimpleUploadedFile(
            "catalogue.xlsx",
            payload.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        response = self.client.post(
            reverse("assistant_core:catalog_import"),
            {"file": upload, "create_aliases": "on", "update_existing": "on"},
        )

        self.assertEqual(response.status_code, 200)
        brand = Brand.objects.get(name="Dolce & Gabbana")
        perfume = Perfume.objects.get(brand=brand, name="Light Blue")
        variant = PerfumeVariant.objects.get(perfume=perfume, size_ml="100.00")
        self.assertEqual(perfume.concentration, "edt")
        self.assertEqual(perfume.audience, "women")
        self.assertEqual(variant.sku, "DG-LB-100")
        self.assertTrue(BrandAlias.objects.filter(brand=brand, alias_text="Dolce & Gabbana").exists())
        self.assertTrue(ProductAlias.objects.filter(perfume=perfume, alias_text="Light Blue").exists())
