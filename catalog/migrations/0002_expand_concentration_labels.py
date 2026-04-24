from django.db import migrations


CONCENTRATION_LABELS = {
    "edp": "Eau de Parfum",
    "edt": "Eau de Toilette",
    "edc": "Eau de Cologne",
    "extrait": "Extrait de Parfum",
    "parfum": "Parfum",
    "perfume_oil": "Perfume Oil",
}


def expand_concentrations(apps, schema_editor):
    Perfume = apps.get_model("catalog", "Perfume")
    for short_value, full_value in CONCENTRATION_LABELS.items():
        Perfume.objects.filter(concentration__iexact=short_value).update(concentration=full_value)


def shrink_concentrations(apps, schema_editor):
    Perfume = apps.get_model("catalog", "Perfume")
    for short_value, full_value in CONCENTRATION_LABELS.items():
        Perfume.objects.filter(concentration=full_value).update(concentration=short_value)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(expand_concentrations, shrink_concentrations),
    ]
