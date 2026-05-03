from django.db import migrations


BRAND_NAME = "Baldessarini"
ALIASES = (
    ("boss baldessarini", 10),
    ("hugo boss baldessarini", 15),
)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    for alias_text, priority in ALIASES:
        BrandAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            defaults={
                "brand": brand,
                "normalized_alias": alias_text,
                "is_regex": False,
                "active": True,
                "priority": priority,
            },
        )


def unseed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    BrandAlias.objects.filter(
        brand=brand,
        supplier=None,
        alias_text__in=[alias_text for alias_text, _priority in ALIASES],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0039_seed_ex_nihilo_fleur_narcotigue_alias"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
