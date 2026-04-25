from __future__ import annotations

from django.db import migrations


RULE_KIND = "regex_preprocess"
RULE_TEXT = r"\(\s*l\s*\) => woman"


def seed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.update_or_create(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        defaults={
            "title": "Audience marker: (L) means woman",
            "scope_value": "",
            "priority": 15,
            "confidence": 100,
            "active": True,
            "approved": True,
        },
    )


def unseed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(rule_kind=RULE_KIND, scope_type="global", rule_text=RULE_TEXT).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0004_seed_worn_garbage_keywords"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
