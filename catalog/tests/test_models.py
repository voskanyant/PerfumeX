from django.test import TestCase

from catalog.models import Brand, Collection, Perfume, PerfumeVariant


class CatalogModelTests(TestCase):
    def test_brand_and_perfume_slugs_are_created(self):
        brand = Brand.objects.create(name="Dolce & Gabbana")
        perfume = Perfume.objects.create(
            brand=brand, name="Light Blue", concentration="Eau de Toilette"
        )

        self.assertTrue(brand.slug)
        self.assertTrue(perfume.slug)
        self.assertFalse(perfume.is_published)

    def test_variant_identity_is_supported(self):
        brand = Brand.objects.create(name="Example")
        perfume = Perfume.objects.create(brand=brand, name="Example Scent")
        variant = PerfumeVariant.objects.create(
            perfume=perfume, size_ml="100.00", packaging="box", variant_type="standard"
        )

        self.assertEqual(variant.perfume, perfume)

    def test_collection_is_brand_scoped(self):
        first_brand = Brand.objects.create(name="Brand A")
        second_brand = Brand.objects.create(name="Brand B")
        first = Collection.objects.create(brand=first_brand, name="Private Collection")
        second = Collection.objects.create(
            brand=second_brand, name="Private Collection"
        )

        self.assertEqual(first.normalized_name, second.normalized_name)
        self.assertNotEqual(first.brand, second.brand)

    def test_perfume_collection_name_creates_brand_collection(self):
        brand = Brand.objects.create(name="Amouage")
        perfume = Perfume.objects.create(
            brand=brand, name="Lineage", collection_name="Odyssey"
        )

        self.assertEqual(perfume.collection.name, "Odyssey")
        self.assertEqual(perfume.collection.brand, brand)

    def test_perfume_collection_name_edit_updates_brand_collection(self):
        brand = Brand.objects.create(name="Amouage")
        perfume = Perfume.objects.create(
            brand=brand, name="Lineage", collection_name="Odyssey"
        )

        perfume.collection_name = "Odyssey Escape"
        perfume.save()
        perfume.refresh_from_db()

        self.assertEqual(perfume.collection_name, "Odyssey Escape")
        self.assertEqual(perfume.collection.name, "Odyssey Escape")
        self.assertEqual(perfume.collection.brand, brand)

    def test_perfume_collection_edit_updates_collection_name(self):
        brand = Brand.objects.create(name="Amouage")
        perfume = Perfume.objects.create(
            brand=brand, name="Lineage", collection_name="Odyssey"
        )
        escape = Collection.objects.create(brand=brand, name="Odyssey Escape")

        perfume.collection = escape
        perfume.save()
        perfume.refresh_from_db()

        self.assertEqual(perfume.collection_name, "Odyssey Escape")
        self.assertEqual(perfume.collection, escape)

    def test_variant_sku_is_generated_when_blank(self):
        brand = Brand.objects.create(name="Dolce & Gabbana")
        perfume = Perfume.objects.create(
            brand=brand, name="Light Blue", concentration="Eau de Toilette"
        )

        variant = PerfumeVariant.objects.create(
            perfume=perfume, size_ml="100.00", variant_type="standard"
        )

        self.assertTrue(variant.sku)
        self.assertIn("DOLCE-GABBANA-LIGHT-BLUE", variant.sku)

    def test_generated_variant_sku_is_unique(self):
        brand = Brand.objects.create(name="Example")
        perfume = Perfume.objects.create(brand=brand, name="Example Scent")
        first = PerfumeVariant.objects.create(
            perfume=perfume, size_ml="100.00", variant_type="standard"
        )
        second = PerfumeVariant.objects.create(
            perfume=perfume, size_ml="100.00", variant_type="standard", packaging="box"
        )

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
