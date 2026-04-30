from __future__ import annotations

from django.db import migrations


RULE_TITLE = "Normalize spaced decimal dots"
RULE_KIND = "regex_preprocess"
RULE_TEXT = r"(?<=\d)\s*\.\s*(?=\d) => ."


def seed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.update_or_create(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        defaults={
            "title": RULE_TITLE,
            "scope_value": "",
            "priority": 18,
            "approved": True,
            "active": True,
            "examples_json": [
                {
                    "before": "PINK MOLeCULE 090 . 09 edp 100 ml",
                    "after": "PINK MOLeCULE 090.09 edp 100 ml",
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
        ("assistant_linking", "0022_seed_dunhill_signature_collection"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
