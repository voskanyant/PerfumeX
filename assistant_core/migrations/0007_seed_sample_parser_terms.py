from __future__ import annotations

from django.db import migrations


RULE_KIND = "parser_sample_term"
SAMPLE_TERMS = (
    "sample",
    "\u043f\u0440\u043e\u0431\u043d\u0438\u043a",
    "vial",
    "\u043f\u0440\u043e\u0431\u0438\u0440\u043a\u0430",
)


def seed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for term in SAMPLE_TERMS:
        GlobalRule.objects.update_or_create(
            rule_kind=RULE_KIND,
            scope_type="global",
            rule_text=term,
            defaults={
                "title": f"Sample term: {term}",
                "scope_value": "",
                "priority": 50,
                "confidence": 100,
                "active": True,
                "approved": True,
            },
        )


def unseed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(rule_kind=RULE_KIND, scope_type="global", rule_text__in=SAMPLE_TERMS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0006_seed_fem_audience_alias"),
    ]

    operations = [
        migrations.RunPython(seed_rules, unseed_rules),
    ]
