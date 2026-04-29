import re
import unicodedata

from django.db import migrations


BRAND_NAME = "Alexandre J."
BRAND_ALIASES = (
    ("Alexandre. J", 20),
    ("Alexandre J", 25),
    ("ALEXANDRE. J", 20),
)
COLLECTION_ALIASES = (
    ("art deco", "The Art Deco Collector", 20),
    ("the art deco collector", "The Art Deco Collector", 20),
    ("art nouveau", "Art Nouveau Collection", 25),
    ("art nouveau collection", "Art Nouveau Collection", 20),
    ("atelier d'artistes", "Atelier D'Artistes", 20),
    ("atelier d artistes", "Atelier D'Artistes", 20),
    ("elixir collection", "Elixir Collection", 20),
    ("the collector", "The Collector", 20),
    ("ultimate collection", "Ultimate Collection", 20),
    ("discontinued", "Discontinued", 80),
    ("atelier d'artistes discontinued", "Atelier D'Artistes - discontinued", 20),
    ("atelier d artistes discontinued", "Atelier D'Artistes - discontinued", 20),
    ("legacy discontinued", "Legacy - discontinued", 20),
    ("oscent discontinued", "Oscent - discontinued", 20),
    ("ultimate collection discontinued", "Ultimate Collection - discontinued", 20),
    ("western leather discontinued", "Western Leather - discontinued", 20),
)


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[\u00a0_\\/,;:|()\[\]{}+]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def seed_alexandre_j_collections(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    for alias_text, priority in BRAND_ALIASES:
        BrandAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            brand=brand,
            defaults={
                "normalized_alias": normalize_alias(alias_text),
                "active": True,
                "priority": priority,
                "is_regex": False,
            },
        )
    for alias_text, collection_name, priority in COLLECTION_ALIASES:
        ProductAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            brand=brand,
            defaults={
                "canonical_text": "",
                "collection_name": collection_name,
                "concentration": "",
                "audience": "",
                "excluded_terms": "",
                "active": True,
                "priority": priority,
            },
        )


def unseed_alexandre_j_collections(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    BrandAlias.objects.filter(
        brand=brand,
        supplier=None,
        alias_text__in=[alias_text for alias_text, _priority in BRAND_ALIASES],
    ).delete()
    ProductAlias.objects.filter(
        brand=brand,
        supplier=None,
        alias_text__in=[alias_text for alias_text, _collection_name, _priority in COLLECTION_ALIASES],
        canonical_text="",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0025_seed_abbreviated_brand_aliases"),
    ]

    operations = [
        migrations.RunPython(seed_alexandre_j_collections, unseed_alexandre_j_collections),
    ]
