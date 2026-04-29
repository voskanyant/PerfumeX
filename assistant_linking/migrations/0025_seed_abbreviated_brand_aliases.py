import re
import unicodedata

from django.db import migrations


BRAND_ALIASES = (
    ("a.banderas", "Antonio Banderas", 40),
    ("a. banderas", "Antonio Banderas", 40),
    ("a banderas", "Antonio Banderas", 45),
    ("banderas", "Antonio Banderas", 45),
    ("s.ferragamo", "Salvatore Ferragamo", 40),
    ("s. ferragamo", "Salvatore Ferragamo", 40),
    ("s ferragamo", "Salvatore Ferragamo", 45),
    ("s.ferregamo", "Salvatore Ferragamo", 35),
    ("s. ferregamo", "Salvatore Ferragamo", 35),
    ("s ferregamo", "Salvatore Ferragamo", 40),
    ("ferregamo", "Salvatore Ferragamo", 45),
    ("a.basi", "Armand Basi", 40),
    ("a. basi", "Armand Basi", 40),
    ("a basi", "Armand Basi", 45),
    ("basi", "Armand Basi", 45),
    ("c. dior", "Dior", 40),
    ("c dior", "Dior", 45),
)


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[\u00a0_\\/,;:|()\[\]{}+]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def seed_abbreviated_brand_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    for alias_text, brand_name, priority in BRAND_ALIASES:
        brand = Brand.objects.filter(name__iexact=brand_name).first()
        if not brand:
            continue
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


def unseed_abbreviated_brand_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    for alias_text, brand_name, _priority in BRAND_ALIASES:
        brand = Brand.objects.filter(name__iexact=brand_name).first()
        if brand:
            BrandAlias.objects.filter(alias_text=alias_text, supplier=None, brand=brand).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0024_normalizationstatssnapshot_bag_count"),
    ]

    operations = [
        migrations.RunPython(seed_abbreviated_brand_aliases, unseed_abbreviated_brand_aliases),
    ]
