from __future__ import annotations

import re
import unicodedata

from django.db import migrations


CONCENTRATION_ALIASES = (
    ("eau de parf", "Eau de Parfum", 90),
    ("парф вода", "Eau de Parfum", 90),
    ("парф. вода", "Eau de Parfum", 90),
    ("парфюмированая", "Eau de Parfum", 95),
    ("парфюмированая вода", "Eau de Parfum", 95),
    ("парфюмированная", "Eau de Parfum", 95),
    ("парфюмированная вода", "Eau de Parfum", 95),
    ("туалетная вода", "Eau de Toilette", 90),
    ("туалетная", "Eau de Toilette", 110),
    ("одеколон", "Eau de Cologne", 90),
    ("cologne", "Eau de Cologne", 110),
    ("pure perfume", "Extrait de Parfum", 80),
    ("parfume", "Extrait de Parfum", 90),
    ("perfume", "Extrait de Parfum", 110),
    ("духи", "Extrait de Parfum", 120),
    ("exdp", "Extrait de Parfum", 95),
    ("parfum oil", "Perfume Oil", 70),
    ("perfume oil", "Perfume Oil", 70),
    ("perfume attar", "Perfume Oil", 70),
    ("масляные духи", "Perfume Oil", 70),
    ("духи масляные", "Perfume Oil", 70),
    ("парфюмированное масло", "Perfume Oil", 70),
    ("attar", "Perfume Oil", 80),
    ("аттар", "Perfume Oil", 80),
)

REMOVED_CONCENTRATION_ALIASES = (
    "парф",
    "cologne absolue",
    "cologne intense",
    "roll on",
    "roll-on",
)

BRAND_ALIASES = (
    ("ysl", "Yves Saint Laurent", 80),
    ("c.dior", "Dior", 80),
    ("d&g", "Dolce & Gabbana", 80),
    ("dolce&gabbana", "Dolce & Gabbana", 90),
    ("dolce gabbana", "Dolce & Gabbana", 90),
    ("viktor&rolf", "Viktor & Rolf", 90),
    ("viktor rolf", "Viktor & Rolf", 90),
    ("zadig&voltaire", "Zadig & Voltaire", 90),
    ("zadig voltaire", "Zadig & Voltaire", 90),
    ("roos&roos", "Roos & Roos", 90),
    ("roos roos", "Roos & Roos", 95),
    ("astrophil&stella", "Astrophil & Stella", 90),
    ("goldfield banks", "Goldfield & Banks", 90),
    ("philly&phill", "Philly & Phill", 90),
    ("philly phill", "Philly & Phill", 90),
    ("m.micallef", "M. Micallef", 90),
    ("s.t.dupont", "S.T. Dupont", 90),
    ("mercedes benz", "Mercedes-Benz", 90),
    ("marc antoine barrois", "Marc-Antoine Barrois", 90),
    ("2787", "27 87", 90),
    ("12parfumeurs", "12 Parfumeurs", 90),
    ("норана", "Norana Perfumes", 85),
    ("noran", "Norana Perfumes", 90),
    ("экс нихило", "Ex Nihilo", 85),
    ("марли", "Parfums de Marly", 85),
)


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"\b(edp|edt|edc)(?=\d)", r"\1 ", text)
    text = re.sub(r"\b(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)(?=\d)", r"\1 ", text)
    text = re.sub(r"[\u00a0_/,;:|()\[\]{}]+", " ", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return re.sub(r"\s+", " ", text).strip()


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ConcentrationAlias = apps.get_model("assistant_linking", "ConcentrationAlias")

    ConcentrationAlias.objects.filter(supplier__isnull=True, alias_text__in=REMOVED_CONCENTRATION_ALIASES).delete()
    ConcentrationAlias.objects.filter(supplier__isnull=True, alias_text="parfum", concentration="Parfum").update(
        concentration="Extrait de Parfum"
    )

    for alias_text, concentration, priority in CONCENTRATION_ALIASES:
        ConcentrationAlias.objects.get_or_create(
            alias_text=alias_text,
            supplier=None,
            concentration=concentration,
            defaults={
                "normalized_alias": normalize_alias(alias_text),
                "active": True,
                "priority": priority,
                "is_regex": False,
            },
        )

    for alias_text, brand_name, priority in BRAND_ALIASES:
        brand = Brand.objects.filter(name__iexact=brand_name).first()
        if not brand:
            continue
        BrandAlias.objects.get_or_create(
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


def unseed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    BrandAlias = apps.get_model("assistant_linking", "BrandAlias")
    ConcentrationAlias = apps.get_model("assistant_linking", "ConcentrationAlias")

    for alias_text, concentration, _priority in CONCENTRATION_ALIASES:
        ConcentrationAlias.objects.filter(alias_text=alias_text, supplier=None, concentration=concentration).delete()
    ConcentrationAlias.objects.filter(supplier__isnull=True, alias_text="parfum", concentration="Extrait de Parfum").update(
        concentration="Parfum"
    )

    for alias_text, brand_name, _priority in BRAND_ALIASES:
        brand = Brand.objects.filter(name__iexact=brand_name).first()
        if brand:
            BrandAlias.objects.filter(alias_text=alias_text, supplier=None, brand=brand).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0007_link_action_history"),
        ("catalog", "0002_expand_concentration_labels"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
