from django.db import migrations


BRAND_NAME = "12 Parfumeurs"
BRAND_ALIASES = (
    ("12 parfumeurs francais", "12 parfumeurs francais"),
    ("12 parfumeurs français", "12 parfumeurs francais"),
)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
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
            },
        )


def unseed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    BrandAlias.objects.filter(
        alias_text__in=[alias_text for alias_text, _normalized_alias in BRAND_ALIASES],
        supplier=None,
        brand=brand,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0033_fragranticaproduct_collection"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
