from django.test import SimpleTestCase

from prices.services.catalog_formatting import normalize_catalogue_collection_name
from prices.services.catalog_formatting import normalize_catalogue_perfume_name


class CatalogueFormattingServiceTests(SimpleTestCase):
    def test_collection_title_case_preserves_known_acronyms_and_small_words(self):
        self.assertEqual(
            normalize_catalogue_collection_name("LEGACY WB AND ORIENTAL II"),
            "Legacy WB and Oriental II",
        )

    def test_perfume_name_folds_diacritics_and_apostrophes(self):
        self.assertEqual(
            normalize_catalogue_perfume_name("L´air Barbès"),
            "L'air Barbes",
        )
