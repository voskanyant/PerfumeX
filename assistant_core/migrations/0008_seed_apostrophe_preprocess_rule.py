from __future__ import annotations

from django.db import migrations


RULE_KIND = "regex_preprocess"
RULE_TEXT = r"(?<=[\p{L}])\s*[`´‘’ʼʹʽ]\s*(?=[\p{L}]) => '"
RULE_TITLE = "Normalize apostrophe-like marks between letters"


def seed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.update_or_create(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        defaults={
            "title": RULE_TITLE,
            "scope_value": "",
            "examples_json": [
                "STATE OF MIND L ` Ame Slave edp 100 ml",
                "STATE OF MIND - L`Ame Slave edp 1,5 ml",
                "L\u2019Ame Slave",
            ],
            "priority": 15,
            "confidence": 95,
            "active": True,
            "approved": True,
        },
    )


def unseed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(rule_kind=RULE_KIND, scope_type="global", rule_text=RULE_TEXT).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0007_seed_sample_parser_terms"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
