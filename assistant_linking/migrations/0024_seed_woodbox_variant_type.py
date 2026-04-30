from __future__ import annotations

from django.db import migrations


RULE_TITLE = "Variant type: woodbox"
RULE_KIND = "parser_variant_type_term"
RULE_TEXT = "woodbox => woodbox"


def seed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.update_or_create(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        defaults={
            "title": RULE_TITLE,
            "scope_value": "",
            "priority": 40,
            "approved": True,
            "active": True,
            "examples_json": [
                {
                    "before": "AFNAN TRIBUTE BLUE WOODBOX 100ml edP",
                    "after": "Afnan / Tribute Blue / Eau de Parfum / 100ml / Woodbox",
                }
            ],
        },
    )


def unseed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(rule_kind=RULE_KIND, scope_type="global", rule_text=RULE_TEXT).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0001_initial"),
        ("assistant_linking", "0023_seed_spaced_decimal_preprocess_rule"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
