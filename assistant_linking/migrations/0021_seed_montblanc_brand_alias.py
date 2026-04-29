import re

from django.db import migrations


def _compact(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def seed_montblanc_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = next((item for item in Brand.objects.all() if _compact(item.name) == "montblanc"), None)
    if not brand:
        return
    BrandAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text="MONT BLANC",
        defaults={
            "normalized_alias": "mont blanc",
            "priority": 20,
            "active": True,
            "is_regex": False,
        },
    )
    ProductAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text="signature",
        defaults={
            "canonical_text": "",
            "collection_name": "Signature",
            "concentration": "",
            "audience": "",
            "excluded_terms": "",
            "active": True,
            "priority": 30,
        },
    )


def unseed_montblanc_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand_ids = [item.id for item in Brand.objects.all() if _compact(item.name) == "montblanc"]
    BrandAlias.objects.filter(
        brand_id__in=brand_ids,
        supplier=None,
        alias_text="MONT BLANC",
        normalized_alias="mont blanc",
    ).delete()
    ProductAlias.objects.filter(
        brand_id__in=brand_ids,
        supplier=None,
        alias_text="signature",
        canonical_text="",
        collection_name="Signature",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_expand_concentration_labels"),
        ("assistant_linking", "0020_seed_van_cleef_collection_extraordinaire"),
    ]

    operations = [
        migrations.RunPython(seed_montblanc_alias, unseed_montblanc_alias),
    ]
