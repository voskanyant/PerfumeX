from __future__ import annotations

import re
import unicodedata

from django.db import migrations


ALIASES = ("тв", "т в", "т/в", "т.в", "т.в.")
CONCENTRATION = "Eau de Toilette"


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[\u00a0_/,;:|()\[\]{}]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def seed_aliases(apps, schema_editor):
    ConcentrationAlias = apps.get_model("assistant_linking", "ConcentrationAlias")
    for priority, alias_text in enumerate(ALIASES, start=20):
        ConcentrationAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            concentration=CONCENTRATION,
            defaults={
                "normalized_alias": normalize_alias(alias_text),
                "is_regex": False,
                "priority": priority,
                "active": True,
            },
        )


def unseed_aliases(apps, schema_editor):
    ConcentrationAlias = apps.get_model("assistant_linking", "ConcentrationAlias")
    ConcentrationAlias.objects.filter(
        supplier=None,
        concentration=CONCENTRATION,
        alias_text__in=ALIASES,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0049_seed_zarkoperfume_purple_molecule_alias"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
