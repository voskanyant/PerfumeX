import re
import unicodedata

from django.db import migrations
from django.utils.text import slugify


BRAND_NAME = "Maison Alhambra"
BRAND_ALIASES = (
    ("AlHambra", 20),
)


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(?<!\d)\.(?!\d)", " ", text)
    text = re.sub(r"[\u00a0_&/,;:|()\[\]{}]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def unique_brand_slug(Brand, value: str) -> str:
    base = slugify(value or "") or "brand"
    slug = base
    counter = 2
    while Brand.objects.filter(slug=slug).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def get_or_create_brand(Brand, name: str):
    brand = Brand.objects.filter(name__iexact=name).first()
    if brand:
        if not brand.slug:
            brand.slug = unique_brand_slug(Brand, brand.name)
            brand.save(update_fields=["slug"])
        return brand
    return Brand.objects.create(name=name, slug=unique_brand_slug(Brand, name))


def seed_maison_alhambra_short_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    brand = get_or_create_brand(Brand, BRAND_NAME)
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


def unseed_maison_alhambra_short_alias(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    brand = Brand.objects.filter(name__iexact=BRAND_NAME).first()
    if not brand:
        return
    BrandAlias.objects.filter(
        brand=brand,
        supplier=None,
        alias_text__in=[alias_text for alias_text, _priority in BRAND_ALIASES],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0058_refresh_linked_apostrophe_perfume_names"),
    ]

    operations = [
        migrations.RunPython(
            seed_maison_alhambra_short_alias,
            unseed_maison_alhambra_short_alias,
        ),
    ]
