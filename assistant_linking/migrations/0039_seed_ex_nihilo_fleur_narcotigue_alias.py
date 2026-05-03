from django.db import migrations


BRAND_NAME = "Ex Nihilo"
ALIAS_TEXT = "fleur narcotigue"
CANONICAL_TEXT = "Fleur Narcotique"


def seed_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    perfume = (
        brand.perfumes.filter(name__iexact=CANONICAL_TEXT).order_by("id").first()
    )
    ProductAlias.objects.update_or_create(
        alias_text=ALIAS_TEXT,
        supplier=None,
        brand=brand,
        defaults={
            "perfume": perfume,
            "canonical_text": CANONICAL_TEXT,
            "collection_name": "",
            "concentration": "",
            "audience": "",
            "excluded_terms": "",
            "active": True,
            "priority": 25,
        },
    )


def unseed_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    ProductAlias.objects.filter(
        alias_text=ALIAS_TEXT,
        supplier=None,
        brand=brand,
        canonical_text=CANONICAL_TEXT,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0038_seed_wom_audience_rule"),
    ]

    operations = [
        migrations.RunPython(seed_alias, unseed_alias),
    ]
