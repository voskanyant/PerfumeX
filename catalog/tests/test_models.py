from django.test import TestCase

from catalog.models import Brand, Perfume, PerfumeVariant


class CatalogModelTests(TestCase):
    def test_brand_and_perfume_slugs_are_created(self):
        brand = Brand.objects.create(name="Dolce & Gabbana")
        perfume = Perfume.objects.create(brand=brand, name="Light Blue", concentration="Eau de Toilette")

        self.assertTrue(brand.slug)
        self.assertTrue(perfume.slug)
        self.assertFalse(perfume.is_published)

    def test_variant_identity_is_supported(self):
        brand = Brand.objects.create(name="Example")
        perfume = Perfume.objects.create(brand=brand, name="Example Scent")
        variant = PerfumeVariant.objects.create(perfume=perfume, size_ml="100.00", packaging="box", variant_type="standard")

        self.assertEqual(variant.perfume, perfume)

    def test_variant_sku_is_generated_when_blank(self):
        brand = Brand.objects.create(name="Dolce & Gabbana")
        perfume = Perfume.objects.create(brand=brand, name="Light Blue", concentration="Eau de Toilette")

        variant = PerfumeVariant.objects.create(perfume=perfume, size_ml="100.00", variant_type="standard")

        self.assertTrue(variant.sku)
        self.assertIn("DOLCE-GABBANA-LIGHT-BLUE", variant.sku)

    def test_generated_variant_sku_is_unique(self):
        brand = Brand.objects.create(name="Example")
        perfume = Perfume.objects.create(brand=brand, name="Example Scent")
        first = PerfumeVariant.objects.create(perfume=perfume, size_ml="100.00", variant_type="standard")
        second = PerfumeVariant.objects.create(perfume=perfume, size_ml="100.00", variant_type="standard", packaging="box")

        self.assertNotEqual(first.sku, second.sku)

    def test_variant_display_size_uses_compact_ml_format(self):
        brand = Brand.objects.create(name="Example")
        perfume = Perfume.objects.create(brand=brand, name="Example Scent")
        variant = PerfumeVariant.objects.create(
            perfume=perfume,
            size_ml="100.00",
            variant_type="standard",
        )

        self.assertEqual(variant.display_size, "100ml")
        self.assertEqual(str(variant), "Example / Example Scent / 100ml")

    def test_variant_display_size_trims_trailing_zeroes(self):
        brand = Brand.objects.create(name="Example")
        perfume = Perfume.objects.create(brand=brand, name="Example Scent")
        variant = PerfumeVariant.objects.create(
            perfume=perfume,
            size_ml="50.00",
            variant_type="standard",
        )

        self.assertEqual(variant.display_size, "50ml")
        self.assertEqual(str(variant), "Example / Example Scent / 50ml")
