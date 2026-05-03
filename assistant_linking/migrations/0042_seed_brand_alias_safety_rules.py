from django.db import migrations


RALPH_LAUREN_ALIASES = (
    ("R.L.", "r.l.", 40),
    ("R.L", "r.l", 40),
    ("R L", "r l", 45),
    ("RL", "rl", 50),
)
CLIVE_CHRISTIAN_COLLECTION_ALIASES = (
    ("private collection", "Private Collection"),
    ("original collection", "Original Collection"),
    ("noble collection", "Noble Collection"),
)
ATTAR_COLLECTION_ALIASES = (("Attar", "attar", 35),)
REMOVED_BARE_OIL_CONCENTRATION_ALIASES = (
    "attar",
    "аттар",
)


def seed_alias_safety_rules(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ConcentrationAlias = apps.get_model("assistant_linking", "ConcentrationAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    attar = Brand.objects.filter(name__iexact="Attar Collection").first()
    if attar:
        BrandAlias.objects.filter(brand=attar, normalized_alias="collection").update(
            active=False
        )
        BrandAlias.objects.filter(brand=attar, alias_text__iexact="collection").update(
            active=False
        )
        for alias_text, normalized_alias, priority in ATTAR_COLLECTION_ALIASES:
            BrandAlias.objects.update_or_create(
                alias_text=alias_text,
                supplier=None,
                brand=attar,
                defaults={
                    "normalized_alias": normalized_alias,
                    "active": True,
                    "priority": priority,
                },
            )
    ConcentrationAlias.objects.filter(
        supplier=None,
        normalized_alias__in=REMOVED_BARE_OIL_CONCENTRATION_ALIASES,
        concentration="Perfume Oil",
    ).update(active=False)
    ConcentrationAlias.objects.filter(
        supplier=None,
        alias_text__in=REMOVED_BARE_OIL_CONCENTRATION_ALIASES,
        concentration="Perfume Oil",
    ).update(active=False)

    ralph_lauren = Brand.objects.filter(name__iexact="Ralph Lauren").first()
    if ralph_lauren:
        for alias_text, normalized_alias, priority in RALPH_LAUREN_ALIASES:
            BrandAlias.objects.update_or_create(
                alias_text=alias_text,
                supplier=None,
                brand=ralph_lauren,
                defaults={
                    "normalized_alias": normalized_alias,
                    "active": True,
                    "priority": priority,
                },
            )

    clive_christian = Brand.objects.filter(name__iexact="Clive Christian").first()
    if clive_christian:
        for alias_text, collection_name in CLIVE_CHRISTIAN_COLLECTION_ALIASES:
            ProductAlias.objects.update_or_create(
                alias_text=alias_text,
                supplier=None,
                brand=clive_christian,
                defaults={
                    "canonical_text": "",
                    "collection_name": collection_name,
                    "concentration": "",
                    "audience": "",
                    "excluded_terms": "",
                    "active": True,
                    "priority": 30,
                },
            )


def unseed_alias_safety_rules(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    attar = Brand.objects.filter(name__iexact="Attar Collection").first()
    if attar:
        BrandAlias.objects.filter(
            brand=attar,
            supplier=None,
            alias_text__in=[
                alias_text
                for alias_text, _normalized_alias, _priority in ATTAR_COLLECTION_ALIASES
            ],
        ).delete()
    ralph_lauren = Brand.objects.filter(name__iexact="Ralph Lauren").first()
    if ralph_lauren:
        BrandAlias.objects.filter(
            brand=ralph_lauren,
            supplier=None,
            alias_text__in=[
                alias_text
                for alias_text, _normalized, _priority in RALPH_LAUREN_ALIASES
            ],
        ).delete()
    clive_christian = Brand.objects.filter(name__iexact="Clive Christian").first()
    if clive_christian:
        ProductAlias.objects.filter(
            brand=clive_christian,
            supplier=None,
            alias_text__in=[
                alias_text
                for alias_text, _collection_name in CLIVE_CHRISTIAN_COLLECTION_ALIASES
            ],
            canonical_text="",
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0041_seed_inspired_by_garbage_rule"),
    ]

    operations = [
        migrations.RunPython(seed_alias_safety_rules, unseed_alias_safety_rules),
    ]
