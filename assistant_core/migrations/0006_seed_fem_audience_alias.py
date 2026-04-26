from __future__ import annotations

from django.db import migrations


RULE_KIND = "parser_audience_term"
RULE_TEXT = "fem => Woman | women"


def seed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.update_or_create(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        defaults={
            "title": "Audience alias: fem => Woman | women",
            "scope_value": "",
            "priority": 80,
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
        ("assistant_core", "0005_seed_parenthetical_l_audience"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
