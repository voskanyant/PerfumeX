from django.db import migrations


def seed_armand_basi_uniform_collection_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = Brand.objects.filter(name__iexact="Armand Basi").first()
    if not brand:
        return
    ProductAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text="uniform",
        defaults={
            "canonical_text": "",
            "collection_name": "Uniform",
            "concentration": "",
            "audience": "",
            "excluded_terms": "",
            "active": True,
            "priority": 30,
        },
    )


def unseed_armand_basi_uniform_collection_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = Brand.objects.filter(name__iexact="Armand Basi").first()
    if not brand:
        return
    ProductAlias.objects.filter(
        brand=brand,
        supplier=None,
        alias_text="uniform",
        canonical_text="",
        collection_name="Uniform",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0028_parsedsupplierproduct_alink_parse_modifiers_gin"),
    ]

    operations = [
        migrations.RunPython(seed_armand_basi_uniform_collection_alias, unseed_armand_basi_uniform_collection_alias),
    ]
