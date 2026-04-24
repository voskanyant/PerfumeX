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
    for model_name in ("ProductAlias", "ParsedSupplierProduct", "MatchGroup"):
        model = apps.get_model("assistant_linking", model_name)
        for short_value, full_value in CONCENTRATION_LABELS.items():
            model.objects.filter(concentration__iexact=short_value).update(concentration=full_value)


def shrink_concentrations(apps, schema_editor):
    for model_name in ("ProductAlias", "ParsedSupplierProduct", "MatchGroup"):
        model = apps.get_model("assistant_linking", model_name)
        for short_value, full_value in CONCENTRATION_LABELS.items():
            model.objects.filter(concentration=full_value).update(concentration=short_value)


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0002_productalias_excluded_terms"),
        ("catalog", "0002_expand_concentration_labels"),
    ]

    operations = [
        migrations.RunPython(expand_concentrations, shrink_concentrations),
    ]
