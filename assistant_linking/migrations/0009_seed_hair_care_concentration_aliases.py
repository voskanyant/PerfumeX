import re
import unicodedata

from django.db import migrations


HAIR_CARE_CONCENTRATION_ALIASES = (
    ("hair mist", "Hair Mist", 70),
    ("hair perfume", "Hair Perfume", 70),
    ("hair fragrance", "Hair Perfume", 75),
    ("\u0434\u044b\u043c\u043a\u0430 \u0434\u043b\u044f \u0432\u043e\u043b\u043e\u0441", "Hair Perfume", 70),
    ("\u0434\u044b\u043c\u043a\u0430 \u0432\u043e\u043b\u043e\u0441", "Hair Perfume", 75),
    ("\u043f\u0430\u0440\u0444\u044e\u043c \u0434\u043b\u044f \u0432\u043e\u043b\u043e\u0441", "Hair Perfume", 70),
    ("\u0430\u0440\u043e\u043c\u0430\u0442 \u0434\u043b\u044f \u0432\u043e\u043b\u043e\u0441", "Hair Perfume", 75),
)


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"\b(edp|edt|edc)(?=\d)", r"\1 ", text)
    text = re.sub(
        r"\b(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)(?=\d)",
        r"\1 ",
        text,
    )
    text = re.sub(r"[\u00a0_/,;:|()\[\]{}]+", " ", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def seed_aliases(apps, schema_editor):
    ConcentrationAlias = apps.get_model("assistant_linking", "ConcentrationAlias")
    for alias_text, concentration, priority in HAIR_CARE_CONCENTRATION_ALIASES:
        ConcentrationAlias.objects.get_or_create(
            supplier=None,
            alias_text=alias_text,
            concentration=concentration,
            defaults={
                "normalized_alias": normalize_alias(alias_text),
                "priority": priority,
                "active": True,
            },
        )


def remove_aliases(apps, schema_editor):
    ConcentrationAlias = apps.get_model("assistant_linking", "ConcentrationAlias")
    for alias_text, concentration, _priority in HAIR_CARE_CONCENTRATION_ALIASES:
        ConcentrationAlias.objects.filter(alias_text=alias_text, supplier=None, concentration=concentration).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0008_seed_kb_alias_corrections"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, remove_aliases),
    ]
