import re
import unicodedata

from django.db import migrations


BRAND_NAME = "Salvador Dali"
BRAND_ALIASES = (
    ("SD", 25),
    ("S.Dali", 35),
    ("S. Dali", 35),
    ("S Dali", 40),
)


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[\u00a0_\\/,;:|()\[\]{}+]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def seed_salvador_dali_brand_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
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


def unseed_salvador_dali_brand_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    BrandAlias.objects.filter(
        alias_text__in=[alias_text for alias_text, _priority in BRAND_ALIASES],
        supplier=None,
        brand=brand,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0029_seed_armand_basi_uniform_collection_alias"),
    ]

    operations = [
        migrations.RunPython(seed_salvador_dali_brand_aliases, unseed_salvador_dali_brand_aliases),
    ]
