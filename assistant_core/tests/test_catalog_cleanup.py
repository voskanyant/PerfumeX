from __future__ import annotations

from django.test import TestCase

from assistant_core.models import KnowledgeNote, SupplierRule
from assistant_core.services.catalog_cleanup import (
    build_catalog_cleanup_context,
    merge_catalog_brand,
    merge_catalog_perfume,
)
from assistant_linking.models import LinkSuggestion, ManualLinkDecision, ProductAlias
from catalog.models import Brand, Perfume, PerfumeVariant
from prices.models import Supplier, SupplierProduct


class CatalogCleanupServiceTests(TestCase):
    def test_build_catalog_cleanup_context_groups_duplicate_keys(self):
        brand = Brand.objects.create(name="Acme!")
        duplicate_brand = Brand.objects.create(name="Acme")
        Perfume.objects.create(brand=brand, name="Amber Musk", concentration="EDP")
        Perfume.objects.create(brand=brand, name="Amber-Musk", concentration="EDP")
        Perfume.objects.create(
            brand=duplicate_brand, name="Amber Musk", concentration="EDP"
        )

        context = build_catalog_cleanup_context()

        brand_duplicate_names = {
            item.name for group in context["brand_duplicates"] for item in group
        }
        perfume_duplicate_names = {
            item.name for group in context["perfume_duplicates"] for item in group
        }
        self.assertEqual(brand_duplicate_names, {"Acme!", "Acme"})
        self.assertEqual(perfume_duplicate_names, {"Amber Musk", "Amber-Musk"})
        self.assertIn("brand_merge_form", context)
        self.assertIn("perfume_merge_form", context)

    def test_merge_catalog_brand_moves_owned_records(self):
        supplier = Supplier.objects.create(name="Supplier A", code="supplier-a")
        source = Brand.objects.create(name="Duplicate Brand")
        target = Brand.objects.create(name="Canonical Brand")
        perfume = Perfume.objects.create(brand=source, name="Sample")
        brand_alias = source.aliases.create(
            alias_text="Duplicate Brand", normalized_alias="duplicate brand"
        )
        product_alias = ProductAlias.objects.create(
            brand=source, alias_text="Sample", canonical_text="Sample"
        )
        note = KnowledgeNote.objects.create(
            category="catalogue", title="Brand note", content="Keep", brand=source
        )
        rule = SupplierRule.objects.create(
            supplier=supplier,
            brand=source,
            title="Brand rule",
            rule_kind="parser",
            rule_text="Keep",
        )

        merge_catalog_brand(source=source, target=target)

        self.assertFalse(Brand.objects.filter(pk=source.pk).exists())
        perfume.refresh_from_db()
        brand_alias.refresh_from_db()
        product_alias.refresh_from_db()
        note.refresh_from_db()
        rule.refresh_from_db()
        self.assertEqual(perfume.brand, target)
        self.assertEqual(brand_alias.brand, target)
        self.assertEqual(product_alias.brand, target)
        self.assertEqual(note.brand, target)
        self.assertEqual(rule.brand, target)

    def test_merge_catalog_perfume_moves_links_and_drops_duplicate_variant(self):
        supplier = Supplier.objects.create(name="Supplier A", code="supplier-a")
        brand = Brand.objects.create(name="Brand")
        source = Perfume.objects.create(brand=brand, name="Duplicate")
        target = Perfume.objects.create(brand=brand, name="Canonical")
        duplicate_variant = PerfumeVariant.objects.create(
            perfume=source, size_ml="100", variant_type="standard"
        )
        moved_variant = PerfumeVariant.objects.create(
            perfume=source, size_ml="50", variant_type="standard"
        )
        PerfumeVariant.objects.create(
            perfume=target, size_ml="100", variant_type="standard"
        )
        product_alias = ProductAlias.objects.create(
            perfume=source,
            brand=source.brand,
            alias_text="Duplicate",
            canonical_text="Duplicate",
        )
        note = KnowledgeNote.objects.create(
            category="catalogue", title="Perfume note", content="Keep", perfume=source
        )
        supplier_product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="supplier-a:duplicate",
            name="Duplicate 50ml",
            catalog_perfume=source,
        )
        decision = ManualLinkDecision.objects.create(
            supplier_product=supplier_product,
            perfume=source,
            decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
        )
        suggestion = LinkSuggestion.objects.create(
            supplier_product=supplier_product,
            suggested_perfume=source,
            confidence=90,
        )

        merge_catalog_perfume(source=source, target=target)

        self.assertFalse(Perfume.objects.filter(pk=source.pk).exists())
        self.assertFalse(
            PerfumeVariant.objects.filter(pk=duplicate_variant.pk).exists()
        )
        moved_variant.refresh_from_db()
        product_alias.refresh_from_db()
        note.refresh_from_db()
        supplier_product.refresh_from_db()
        decision.refresh_from_db()
        suggestion.refresh_from_db()
        self.assertEqual(moved_variant.perfume, target)
        self.assertEqual(product_alias.perfume, target)
        self.assertEqual(product_alias.brand, target.brand)
        self.assertEqual(note.perfume, target)
        self.assertEqual(supplier_product.catalog_perfume, target)
        self.assertEqual(decision.perfume, target)
        self.assertEqual(suggestion.suggested_perfume, target)
