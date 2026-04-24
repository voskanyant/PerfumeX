from django.test import TestCase

from catalog.models import Brand, Perfume, PerfumeVariant


class CatalogModelTests(TestCase):
    def test_brand_and_perfume_slugs_are_created(self):
        brand = Brand.objects.create(name="Dolce & Gabbana")
        perfume = Perfume.objects.create(brand=brand, name="Light Blue", concentration="edt")

        self.assertTrue(brand.slug)
        self.assertTrue(perfume.slug)
        self.assertFalse(perfume.is_published)

    def test_variant_identity_is_supported(self):
        brand = Brand.objects.create(name="Example")
        perfume = Perfume.objects.create(brand=brand, name="Example Scent")
        variant = PerfumeVariant.objects.create(perfume=perfume, size_ml="100.00", packaging="box", variant_type="standard")

        self.assertEqual(variant.perfume, perfume)
