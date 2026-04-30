from django.core.management import call_command
from django.test import TestCase

from assistant_linking.models import FragranticaProduct
from assistant_linking.services.html_catalog_importer import canonical_key, import_brand_catalog, parse_brand_catalog_html
from catalog.models import Brand, Perfume


SAMPLE_HTML = """
<div>
  <h2 class="tw-gridlist-section-title"> All Fragrances </h2>
  <a href="/perfume/Van-Cleef-Arpels/Bois-Dore-1.html" class="group prefumeHbox tw-listview-item-unisex">
    <h3 class="tw-perfume-title"> Bois DorÃƒÂ© </h3>
    <p class="tw-perfume-designer"> Van Cleef &amp; Arpels </p>
    <span class="tw-year-badge"> 2017 </span>
  </a>
  <a href="/perfume/Van-Cleef-Arpels/First-2.html" title="Van Cleef &amp; Arpels First Ð¶ÐµÐ½ÑÐºÐ¸Ð¹ 1976" class="group prefumeHbox">
    <h3 class="tw-perfume-title"> First </h3>
    <p class="tw-perfume-designer"> Van Cleef &amp; Arpels </p>
    <span class="tw-year-badge"> 1976 </span>
  </a>
  <h2 class="tw-gridlist-section-title"> Collection Extraordinaire </h2>
  <a href="/perfume/Van-Cleef-Arpels/Bois-Dore-1.html" class="group prefumeHbox tw-listview-item-unisex">
    <h3 class="tw-perfume-title"> Bois DorÃƒÂ© </h3>
    <p class="tw-perfume-designer"> Van Cleef &amp; Arpels </p>
    <span class="tw-year-badge"> 2017 </span>
  </a>
</div>
"""


class HtmlCatalogImporterTests(TestCase):
    def test_parser_assigns_specific_collection_over_all_fragrances(self):
        items = parse_brand_catalog_html(SAMPLE_HTML)
        by_name = {canonical_key(item.name): item for item in items}

        self.assertEqual(len(items), 2)
        self.assertEqual(sorted(item.audience for item in items), ["Unisex", "Women"])
        self.assertEqual(by_name["bois dore"].collection_name, "Collection Extraordinaire")
        self.assertEqual(by_name["bois dore"].release_year, 2017)
        self.assertEqual(by_name["first"].collection_name, "")

    def test_import_stages_fragrantica_products_without_touching_catalogue(self):
        brand = Brand.objects.create(name="Van Cleef & Arpels")
        perfume = Perfume.objects.create(brand=brand, name="Bois DorÃ©")
        items = parse_brand_catalog_html(SAMPLE_HTML)

        summary = import_brand_catalog(items, apply=True)

        perfume.refresh_from_db()
        self.assertEqual(perfume.collection_name, "")
        self.assertEqual(perfume.audience, "")
        self.assertIsNone(perfume.release_year)
        self.assertEqual(len(summary.missing_items), 2)
        self.assertEqual(len(summary.created_fragrantica_products), 2)
        self.assertTrue(
            FragranticaProduct.objects.filter(
                brand_name="Van Cleef & Arpels",
                normalized_name="bois dore",
                collection_name="Collection Extraordinaire",
                audience="Unisex",
                release_year=2017,
            ).exists()
        )

    def test_import_updates_existing_staged_fragrantica_product(self):
        Brand.objects.create(name="Van Cleef & Arpels")
        FragranticaProduct.objects.create(
            brand_name="Van Cleef & Arpels",
            normalized_brand_name="van cleef and arpels",
            name="Bois Doré",
            normalized_name="bois dore",
            source_path="/perfume/Van-Cleef-Arpels/Bois-Dore-1.html",
        )
        items = parse_brand_catalog_html(SAMPLE_HTML)

        summary = import_brand_catalog(items, apply=True)

        self.assertEqual(len(summary.existing_fragrantica_products), 1)
        self.assertEqual(len(summary.created_fragrantica_products), 1)
        self.assertEqual(len(summary.updated_fragrantica_products), 1)
        self.assertEqual(FragranticaProduct.objects.count(), 2)
        self.assertTrue(
            FragranticaProduct.objects.filter(
                name="Bois DorÃ©",
                collection_name="Collection Extraordinaire",
                audience="Unisex",
                release_year=2017,
            ).exists()
        )

    def test_command_is_dry_run_by_default(self):
        items = parse_brand_catalog_html(SAMPLE_HTML)
        self.assertEqual(len(items), 2)

        call_command("import_brand_catalog_html", "assistant_linking/tests/fixtures/brand_catalog_sample.html", verbosity=0)

        self.assertFalse(Brand.objects.filter(name="Van Cleef & Arpels").exists())
        self.assertFalse(FragranticaProduct.objects.exists())
