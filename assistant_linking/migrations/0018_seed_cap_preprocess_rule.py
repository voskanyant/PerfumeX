from __future__ import annotations

from django.db import migrations


RULE_TITLE = "Remove supplier cap notes"
RULE_KIND = "regex_preprocess"
RULE_TEXT = r"\b(?:с|без)\s+крышк(?:ой|и|а|у)?\b|\b(?:with|without)\s+(?:cap|lid)\b => "


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
                "VERSACE Yellow Diamond edt 90 ml Tester с крышкой",
                "Tester with cap",
            ],
            "priority": 20,
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
        ("assistant_core", "0001_initial"),
        ("assistant_linking", "0017_normalizationstatssnapshot"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
