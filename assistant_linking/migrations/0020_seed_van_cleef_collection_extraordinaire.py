from __future__ import annotations

from django.db import migrations


BRAND_NAME = "Van Cleef & Arpels"
COLLECTION_NAME = "Collection Extraordinaire"
COLLECTION_ALIASES = (
    "collection extraordinaire",
    "extraordinaire",
)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    for alias_text in COLLECTION_ALIASES:
        ProductAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            defaults={
                "brand": brand,
                "canonical_text": "",
                "collection_name": COLLECTION_NAME,
                "concentration": "",
                "audience": "",
                "excluded_terms": "",
                "active": True,
                "priority": 30,
            },
        )


def unseed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if brand:
        ProductAlias.objects.filter(
            alias_text__in=COLLECTION_ALIASES,
            supplier=None,
            brand=brand,
            canonical_text="",
            collection_name=COLLECTION_NAME,
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0019_parsedsupplierproduct_release_year"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
