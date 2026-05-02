from django.db import migrations


BRAND_NAME = "Alexandre J."
ALIASES = (
    ("ultimate legacy wb", "Legacy WB", 25),
    ("legacy wb", "Legacy WB", 30),
)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    for alias_text, canonical_text, priority in ALIASES:
        ProductAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            brand=brand,
            defaults={
                "canonical_text": canonical_text,
                "collection_name": "",
                "concentration": "",
                "audience": "",
                "excluded_terms": "",
                "active": True,
                "priority": priority,
            },
        )


def unseed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    ProductAlias.objects.filter(
        brand=brand,
        supplier=None,
        alias_text__in=[alias_text for alias_text, _canonical_text, _priority in ALIASES],
        canonical_text="Legacy WB",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0034_seed_12_parfumeurs_francais_alias"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
