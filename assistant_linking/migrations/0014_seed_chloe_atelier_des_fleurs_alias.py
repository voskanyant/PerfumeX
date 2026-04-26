from __future__ import annotations

from django.db import migrations


COLLECTION_NAME = "Atelier des Fleurs"
SCENT_NAME = "Jasminum Sambac"
PRODUCT_ALIASES = (
    "atelier jasminum sambac",
    "atelir jasminum sambac",
    "atelier des fleurs jasminum sambac",
)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    brand = Brand.objects.filter(name__iexact="Chloe").first()
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
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    ProductAlias.objects.filter(
        alias_text__in=PRODUCT_ALIASES,
        supplier=None,
        canonical_text=SCENT_NAME,
        collection_name=COLLECTION_NAME,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0013_seed_armani_acqua_di_gioia_intense_alias"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
