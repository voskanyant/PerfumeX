from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from assistant_linking.models import FragranticaProduct
from assistant_linking.services.catalogue_promotion import (
    import_fragrantica_catalogue_link_export,
)
from catalog.models import Brand, Perfume


class FragranticaCataloguePromotionTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Montale")
        self.perfume = Perfume.objects.create(
            brand=self.brand,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
            collection_name="Classic",
        )

    def test_export_command_writes_only_reviewed_linked_rows(self):
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
        )
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Unreviewed",
            normalized_name="unreviewed",
            source_path="/perfume/Montale/Unreviewed.html",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fragrantica-links.json"

            call_command(
                "export_fragrantica_catalogue_links",
                str(path),
                verbosity=0,
            )

            bundle = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["kind"], "perfumex.fragrantica_catalogue_links")
        self.assertEqual(bundle["row_count"], 1)
        self.assertEqual(bundle["rows"][0]["fragrantica"]["name"], "Vanilla Extasy")
        self.assertEqual(bundle["rows"][0]["target"]["perfume_id"], self.perfume.id)

    def test_import_dry_run_reports_link_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_bundle(Path(temp_dir) / "fragrantica-links.json")

            summary = import_fragrantica_catalogue_link_export(path)

        self.assertEqual(summary.linked_sources, 1)
        self.assertFalse(FragranticaProduct.objects.exists())
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.name, "Vanilla Extasy")

    def test_import_apply_links_source_and_preserves_local_concentration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_bundle(Path(temp_dir) / "fragrantica-links.json")

            summary = import_fragrantica_catalogue_link_export(path, apply=True)

        self.assertEqual(summary.created_sources, 1)
        self.assertEqual(summary.linked_sources, 1)
        source = FragranticaProduct.objects.get()
        self.perfume.refresh_from_db()
        self.assertEqual(source.matched_perfume, self.perfume)
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_LINKED)
        self.assertEqual(self.perfume.name, "Vanilla Extasy Source")
        self.assertEqual(self.perfume.collection_name, "Fragrantica Collection")
        self.assertEqual(self.perfume.collection.name, "Fragrantica Collection")
        self.assertEqual(source.collection, self.perfume.collection)
        self.assertEqual(self.perfume.audience, "Women")
        self.assertEqual(self.perfume.release_year, 2008)
        self.assertEqual(self.perfume.concentration, "Eau de Parfum")

    def _write_bundle(self, path: Path) -> Path:
        bundle = {
            "schema_version": 1,
            "kind": "perfumex.fragrantica_catalogue_links",
            "generated_at": "2026-05-02T00:00:00+00:00",
            "row_count": 1,
            "rows": [
                {
                    "fragrantica": {
                        "brand_name": "Montale",
                        "normalized_brand_name": "montale",
                        "name": "Vanilla Extasy Source",
                        "normalized_name": "vanilla extasy source",
                        "collection_name": "Fragrantica Collection",
                        "audience": "Women",
                        "release_year": 2008,
                        "source_path": "/perfume/Montale/Vanilla-Extasy-1.html",
                        "source_url": "https://www.fragrantica.com/perfume/Montale/Vanilla-Extasy-1.html",
                        "source_domain": "fragrantica.com",
                        "match_status": FragranticaProduct.STATUS_LINKED,
                    },
                    "target": {
                        "perfume_id": self.perfume.id,
                        "brand_id": self.brand.id,
                        "brand_name": "Montale",
                        "name": "Vanilla Extasy",
                        "concentration": "Eau de Parfum",
                        "collection_name": "Classic",
                        "audience": "",
                        "release_year": None,
                    },
                }
            ],
        }
        path.write_text(json.dumps(bundle), encoding="utf-8")
        return path
