from __future__ import annotations

from collections import defaultdict
import re

from django.db import transaction

from assistant_core import forms, models
from catalog.models import Brand, Perfume


def catalog_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def build_catalog_cleanup_context(*, brand_merge_form=None, perfume_merge_form=None):
    brand_groups = defaultdict(list)
    for brand in Brand.objects.order_by("name"):
        brand_groups[catalog_key(brand.name)].append(brand)

    perfume_groups = defaultdict(list)
    for perfume in Perfume.objects.select_related("brand").order_by(
        "brand__name", "name"
    ):
        perfume_groups[
            (perfume.brand_id, catalog_key(perfume.name), perfume.concentration or "")
        ].append(perfume)

    return {
        "brand_duplicates": [
            items for items in brand_groups.values() if len(items) > 1
        ],
        "perfume_duplicates": [
            items for items in perfume_groups.values() if len(items) > 1
        ],
        "brand_merge_form": brand_merge_form or forms.CatalogBrandMergeForm(),
        "perfume_merge_form": perfume_merge_form or forms.CatalogPerfumeMergeForm(),
    }


def merge_catalog_brand(*, source, target) -> None:
    with transaction.atomic():
        source.perfumes.update(brand=target)
        source.aliases.update(brand=target)
        source.product_aliases.update(brand=target)
        models.KnowledgeNote.objects.filter(brand=source).update(brand=target)
        models.SupplierRule.objects.filter(brand=source).update(brand=target)
        source.delete()


def merge_catalog_perfume(*, source, target) -> None:
    from assistant_linking.models import LinkSuggestion, ManualLinkDecision
    from prices.models import SupplierProduct

    with transaction.atomic():
        for variant in source.variants.all():
            duplicate = target.variants.filter(
                size_ml=variant.size_ml,
                packaging=variant.packaging,
                variant_type=variant.variant_type,
                is_tester=variant.is_tester,
            ).first()
            if duplicate:
                variant.delete()
            else:
                variant.perfume = target
                variant.save(update_fields=["perfume"])

        source.sources.update(perfume=target)
        source.fact_claims.update(perfume=target)
        source.ai_drafts.update(perfume=target)
        source.perfume_notes.update(perfume=target)
        source.perfume_accords.update(perfume=target)
        source.product_aliases.update(perfume=target, brand=target.brand)
        models.KnowledgeNote.objects.filter(perfume=source).update(perfume=target)

        SupplierProduct.objects.filter(catalog_perfume=source).update(
            catalog_perfume=target
        )
        ManualLinkDecision.objects.filter(perfume=source).update(perfume=target)
        LinkSuggestion.objects.filter(suggested_perfume=source).update(
            suggested_perfume=target
        )
        source.delete()
