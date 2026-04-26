from django.core.management import call_command
from django.test import TestCase

from assistant_linking.models import BrandAlias, ProductAlias
from assistant_linking.services.html_catalog_importer import import_brand_catalog, parse_brand_catalog_html
from catalog.models import Brand, Perfume


SAMPLE_HTML = """
<div>
  <h2 class="tw-gridlist-section-title"> All Fragrances </h2>
  <a href="/perfume/Van-Cleef-Arpels/Bois-Dore-1.html" class="group prefumeHbox">
    <h3 class="tw-perfume-title"> Bois DorÃ© </h3>
    <p class="tw-perfume-designer"> Van Cleef &amp; Arpels </p>
    <span class="tw-year-badge"> 2017 </span>
  </a>
  <a href="/perfume/Van-Cleef-Arpels/First-2.html" class="group prefumeHbox">
    <h3 class="tw-perfume-title"> First </h3>
    <p class="tw-perfume-designer"> Van Cleef &amp; Arpels </p>
    <span class="tw-year-badge"> 1976 </span>
  </a>
  <h2 class="tw-gridlist-section-title"> Collection Extraordinaire </h2>
  <a href="/perfume/Van-Cleef-Arpels/Bois-Dore-1.html" class="group prefumeHbox">
    <h3 class="tw-perfume-title"> Bois DorÃ© </h3>
    <p class="tw-perfume-designer"> Van Cleef &amp; Arpels </p>
    <span class="tw-year-badge"> 2017 </span>
  </a>
</div>
"""


class HtmlCatalogImporterTests(TestCase):
    def test_parser_assigns_specific_collection_over_all_fragrances(self):
        items = parse_brand_catalog_html(SAMPLE_HTML)
        by_name = {item.name: item for item in items}

        self.assertEqual(len(items), 2)
        self.assertEqual(by_name["Bois Doré"].collection_name, "Collection Extraordinaire")
        self.assertEqual(by_name["Bois Doré"].release_year, 2017)
        self.assertEqual(by_name["First"].collection_name, "")

    def test_import_updates_existing_perfume_and_reports_missing(self):
        brand = Brand.objects.create(name="Van Cleef & Arpels")
        perfume = Perfume.objects.create(brand=brand, name="Bois Doré")
        items = parse_brand_catalog_html(SAMPLE_HTML)

        summary = import_brand_catalog(items, apply=True, create_aliases=True)

        perfume.refresh_from_db()
        self.assertEqual(perfume.collection_name, "Collection Extraordinaire")
        self.assertEqual(perfume.release_year, 2017)
        self.assertEqual(len(summary.missing_items), 1)
        self.assertEqual(summary.missing_items[0].name, "First")
        self.assertTrue(BrandAlias.objects.filter(brand=brand, alias_text="Van Cleef & Arpels").exists())
        self.assertTrue(
            ProductAlias.objects.filter(
                brand=brand,
                alias_text="Bois Doré",
                canonical_text="Bois Doré",
                collection_name="Collection Extraordinaire",
            ).exists()
        )

    def test_import_can_create_missing_catalog_perfumes(self):
        Brand.objects.create(name="Van Cleef & Arpels")
        items = parse_brand_catalog_html(SAMPLE_HTML)

        summary = import_brand_catalog(items, apply=True, create_missing_catalog=True)

        self.assertEqual(len(summary.created_perfumes), 2)
        self.assertTrue(Perfume.objects.filter(name="Bois Doré", collection_name="Collection Extraordinaire").exists())
        self.assertTrue(Perfume.objects.filter(name="First", release_year=1976).exists())

    def test_command_is_dry_run_by_default(self):
        items = parse_brand_catalog_html(SAMPLE_HTML)
        self.assertEqual(len(items), 2)

        call_command("import_brand_catalog_html", "assistant_linking/tests/fixtures/brand_catalog_sample.html", verbosity=0)

        self.assertFalse(Brand.objects.filter(name="Van Cleef & Arpels").exists())
