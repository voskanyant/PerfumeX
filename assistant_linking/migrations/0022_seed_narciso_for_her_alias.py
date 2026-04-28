import re

from django.db import migrations


def _compact(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def seed_narciso_for_her_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = next((item for item in Brand.objects.all() if _compact(item.name) == "narcisorodriguez"), None)
    if not brand:
        return
    ProductAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text="for her",
        defaults={
            "canonical_text": "for Her",
            "collection_name": "",
            "concentration": "",
            "audience": "Woman",
            "excluded_terms": "",
            "active": True,
            "priority": 30,
        },
    )


def unseed_narciso_for_her_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand_ids = [item.id for item in Brand.objects.all() if _compact(item.name) == "narcisorodriguez"]
    ProductAlias.objects.filter(
        brand_id__in=brand_ids,
        supplier=None,
        alias_text="for her",
        canonical_text="for Her",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0021_seed_montblanc_brand_alias"),
    ]

    operations = [
        migrations.RunPython(seed_narciso_for_her_alias, unseed_narciso_for_her_alias),
    ]
