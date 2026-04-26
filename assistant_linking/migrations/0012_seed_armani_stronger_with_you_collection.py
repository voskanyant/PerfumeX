from __future__ import annotations

from django.db import migrations


COLLECTION_NAME = "Emporio Armani Stronger With You"
SCENT_NAME = "Amber"
BRAND_ALIASES = (
    "giorgio armani",
    "emporio armani",
)
PRODUCT_ALIASES = (
    "emporio armani stronger with you amber exclusive edi",
    "emporio armani stronger with amber exclusive edi",
    "emporio armani stronger with you amber",
    "emporio armani stronger with amber",
    "stronger with you amber exclusive edi",
    "stronger with amber exclusive edi",
    "stronger with you amber",
    "stronger with amber",
)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    brand = Brand.objects.filter(name__iexact="Armani").first()
    for alias_text in BRAND_ALIASES:
        if not brand:
            continue
        BrandAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            brand=brand,
            defaults={
                "normalized_alias": alias_text,
                "active": True,
                "priority": 20,
                "is_regex": False,
            },
        )

    for alias_text in PRODUCT_ALIASES:
        ProductAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            defaults={
                "brand": brand,
                "canonical_text": SCENT_NAME,
                "collection_name": COLLECTION_NAME,
                "concentration": "",
                "audience": "",
                "excluded_terms": "",
                "active": True,
                "priority": 20,
            },
        )


def unseed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    ProductAlias.objects.filter(
        alias_text__in=PRODUCT_ALIASES,
        supplier=None,
        canonical_text=SCENT_NAME,
        collection_name=COLLECTION_NAME,
    ).delete()

    brand = Brand.objects.filter(name__iexact="Armani").first()
    if brand:
        BrandAlias.objects.filter(alias_text__in=BRAND_ALIASES, supplier=None, brand=brand).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0011_matchgroup_collection_name_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
