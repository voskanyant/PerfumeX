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


def seed_dunhill_signature_collection_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = _find_dunhill_brand(Brand)
    if not brand:
        return
    BrandAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text="A.DUNHILL",
        defaults={
            "normalized_alias": "a.dunhill",
            "priority": 20,
            "active": True,
            "is_regex": False,
        },
    )
    ProductAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text="signature collection",
        defaults={
            "canonical_text": "",
            "collection_name": "Signature Collection",
            "concentration": "",
            "audience": "",
            "excluded_terms": "",
            "active": True,
            "priority": 30,
        },
    )


def unseed_dunhill_signature_collection_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand_ids = [
        item.id
        for item in Brand.objects.all()
        if _compact(item.name) in {"alfreddunhill", "dunhill"}
    ]
    BrandAlias.objects.filter(
        brand_id__in=brand_ids,
        supplier=None,
        alias_text="A.DUNHILL",
        normalized_alias="a.dunhill",
    ).delete()
    ProductAlias.objects.filter(
        brand_id__in=brand_ids,
        supplier=None,
        alias_text="signature collection",
        canonical_text="",
        collection_name="Signature Collection",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0022_seed_narciso_for_her_alias"),
    ]

    operations = [
        migrations.RunPython(seed_dunhill_signature_collection_alias, unseed_dunhill_signature_collection_alias),
    ]
