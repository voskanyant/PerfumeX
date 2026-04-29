import re

from django.db import migrations


def _compact(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _find_dunhill_brand(Brand):
    return next(
        (
            item
            for item in Brand.objects.all()
            if _compact(item.name) in {"alfreddunhill", "dunhill"}
        ),
        None,
    )


def seed_dunhill_desire_red_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = _find_dunhill_brand(Brand)
    if not brand:
        return
    ProductAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text="desire red",
        defaults={
            "canonical_text": "Desire for Men",
            "collection_name": "",
            "concentration": "",
            "audience": "Men",
            "excluded_terms": "",
            "active": True,
            "priority": 25,
        },
    )


def unseed_dunhill_desire_red_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand_ids = [
        item.id
        for item in Brand.objects.all()
        if _compact(item.name) in {"alfreddunhill", "dunhill"}
    ]
    ProductAlias.objects.filter(
        brand_id__in=brand_ids,
        supplier=None,
        alias_text="desire red",
        canonical_text="Desire for Men",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0026_seed_alexandre_j_collections"),
    ]

    operations = [
        migrations.RunPython(seed_dunhill_desire_red_alias, unseed_dunhill_desire_red_alias),
    ]
