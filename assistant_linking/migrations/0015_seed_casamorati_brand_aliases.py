from __future__ import annotations

from django.db import migrations


BRAND_NAME = "Casamorati"
BRAND_ALIASES = (
    "xerjoff casamorati",
    "casamorati",
)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")

    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    for alias_text in BRAND_ALIASES:
        BrandAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            brand=brand,
            defaults={
                "normalized_alias": alias_text,
                "active": True,
                "priority": 10,
                "is_regex": False,
            },
        )


def unseed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")

    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if brand:
        BrandAlias.objects.filter(alias_text__in=BRAND_ALIASES, supplier=None, brand=brand).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0014_seed_chloe_atelier_des_fleurs_alias"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
