from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from assistant_linking.models import FragranticaProduct
from assistant_linking.models import FragranticaProductLink
from assistant_linking.services.catalogue_promotion import (
    import_fragrantica_catalogue_link_export,
)
from catalog.models import Brand, Perfume
from prices.services.catalog_review import (
    apply_fragrantica_identity_to_perfume,
    build_catalogue_fragrantica_candidates_for_perfumes,
    build_catalogue_linking_rows,
    catalogue_linking_perfume_label,
    normalize_catalogue_perfume_name,
    run_fragrantica_catalogue_link_action,
)


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
        source = FragranticaProduct.objects.create(
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
        extra_perfume = Perfume.objects.create(
            brand=self.brand,
            name="Vanilla Extasy",
            concentration="Eau de Toilette",
        )
        FragranticaProductLink.objects.create(
            source=source,
            perfume=extra_perfume,
            link_type=FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
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
        self.assertEqual(bundle["schema_version"], 2)
        self.assertEqual(bundle["row_count"], 2)
        self.assertEqual(bundle["rows"][0]["fragrantica"]["name"], "Vanilla Extasy")
        self.assertEqual(bundle["rows"][0]["target"]["perfume_id"], self.perfume.id)
        self.assertEqual(bundle["rows"][0]["link_type"], "primary")
        self.assertEqual(bundle["rows"][1]["link_type"], "manual_extra")
        self.assertEqual(
            bundle["rows"][1]["target"]["perfume_id"],
            extra_perfume.id,
        )

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

    def test_import_apply_preserves_manual_extra_fragrantica_links(self):
        extra_perfume = Perfume.objects.create(
            brand=self.brand,
            name="Vanilla Extasy",
            concentration="Eau de Toilette",
        )
        bundle = {
            "schema_version": 2,
            "kind": "perfumex.fragrantica_catalogue_links",
            "generated_at": "2026-05-04T00:00:00+00:00",
            "row_count": 2,
            "rows": [
                {
                    "fragrantica": {
                        "brand_name": "Montale",
                        "normalized_brand_name": "montale",
                        "name": "Vanilla Extasy",
                        "normalized_name": "vanilla extasy",
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
                    "link_type": FragranticaProductLink.LINK_TYPE_PRIMARY,
                },
                {
                    "fragrantica": {
                        "brand_name": "Montale",
                        "normalized_brand_name": "montale",
                        "name": "Vanilla Extasy",
                        "normalized_name": "vanilla extasy",
                        "collection_name": "Fragrantica Collection",
                        "audience": "Women",
                        "release_year": 2008,
                        "source_path": "/perfume/Montale/Vanilla-Extasy-1.html",
                        "source_url": "https://www.fragrantica.com/perfume/Montale/Vanilla-Extasy-1.html",
                        "source_domain": "fragrantica.com",
                        "match_status": FragranticaProduct.STATUS_LINKED,
                    },
                    "target": {
                        "perfume_id": extra_perfume.id,
                        "brand_id": self.brand.id,
                        "brand_name": "Montale",
                        "name": "Vanilla Extasy",
                        "concentration": "Eau de Toilette",
                        "collection_name": "",
                        "audience": "",
                        "release_year": None,
                    },
                    "link_type": FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fragrantica-links.json"
            path.write_text(json.dumps(bundle), encoding="utf-8")

            summary = import_fragrantica_catalogue_link_export(path, apply=True)

        self.assertEqual(summary.linked_sources, 2)
        source = FragranticaProduct.objects.get()
        self.assertEqual(source.matched_perfume, self.perfume)
        self.assertTrue(
            FragranticaProductLink.objects.filter(
                source=source,
                perfume=extra_perfume,
                link_type=FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
            ).exists()
        )

    def test_reviewed_uppercase_source_name_is_title_normalized(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="ROSE & BLACK PEPPER",
            normalized_name="rose and black pepper",
            collection_name="L'ATELIER",
            source_path="/perfume/Montale/Rose-Black-Pepper.html",
            match_status=FragranticaProduct.STATUS_LINKED,
        )

        changed_fields = apply_fragrantica_identity_to_perfume(source, self.perfume)

        self.perfume.refresh_from_db()
        self.assertIn("name", changed_fields)
        self.assertEqual(self.perfume.name, "Rose & Black Pepper")
        self.assertEqual(self.perfume.collection_name, "L'Atelier")
        self.assertEqual(
            catalogue_linking_perfume_label(self.perfume),
            "Montale / Rose & Black Pepper / Eau de Parfum",
        )

    def test_catalogue_perfume_name_title_normalization_is_conservative(self):
        self.assertEqual(
            normalize_catalogue_perfume_name("ROSE & BLACK PEPPER"),
            "Rose & Black Pepper",
        )
        self.assertEqual(
            normalize_catalogue_perfume_name("Light Blue Pour Homme"),
            "Light Blue Pour Homme",
        )

    def test_equal_top_fragrantica_source_requires_manual_review(self):
        brand = Brand.objects.create(name="100 Bon")
        edp = Perfume.objects.create(
            brand=brand,
            name="Mirage du Desert",
            concentration="Eau de Parfum",
        )
        edt = Perfume.objects.create(
            brand=brand,
            name="Mirage du Desert",
            concentration="Eau de Toilette",
        )
        FragranticaProduct.objects.create(
            brand_name="100 Bon",
            normalized_brand_name="100 bon",
            name="Mirage du Desert",
            normalized_name="mirage du desert",
            source_path="/perfume/100-Bon/Mirage-du-Desert.html",
        )

        candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
            [edp, edt],
            min_score=80,
        )
        rows = build_catalogue_linking_rows([edp, edt], min_score=80)

        self.assertTrue(candidate_map[edp.id][0].manual_review_reason)
        self.assertTrue(candidate_map[edt.id][0].manual_review_reason)
        self.assertFalse(rows[0]["ready_for_bulk"])
        self.assertFalse(rows[1]["ready_for_bulk"])

    def test_link_action_does_not_reassign_linked_fragrantica_source(self):
        other_perfume = Perfume.objects.create(
            brand=self.brand,
            name="Other Vanilla",
            concentration="Eau de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            source_path="/perfume/Montale/Vanilla-Extasy.html",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
        )

        result = run_fragrantica_catalogue_link_action(
            source.id,
            {
                "perfume_id": str(other_perfume.id),
                "next": "/admin/our-products/linking/",
            },
        )

        source.refresh_from_db()
        self.assertEqual(result.level, "error")
        self.assertEqual(source.matched_perfume, self.perfume)

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
