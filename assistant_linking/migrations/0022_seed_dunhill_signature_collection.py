from __future__ import annotations

from django.db import migrations


BRAND_NAME = "Alfred Dunhill"
BRAND_ALIASES = (
    ("A.DUNHILL", "a.dunhill"),
)
COLLECTION_NAME = "Signature Collection"
COLLECTION_ALIASES = (
    "signature collection",
)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    for alias_text, normalized_alias in BRAND_ALIASES:
        BrandAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            brand=brand,
            defaults={
                "normalized_alias": normalized_alias,
                "active": True,
                "priority": 20,
                "is_regex": False,
            },
        )
    for alias_text in COLLECTION_ALIASES:
        ProductAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            brand=brand,
            defaults={
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
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    BrandAlias.objects.filter(
        alias_text__in=[alias_text for alias_text, _normalized_alias in BRAND_ALIASES],
        supplier=None,
        brand=brand,
    ).delete()
    ProductAlias.objects.filter(
        alias_text__in=COLLECTION_ALIASES,
        supplier=None,
        brand=brand,
        canonical_text="",
        collection_name=COLLECTION_NAME,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0021_fragranticaproduct"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
