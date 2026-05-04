from __future__ import annotations

from django.db import migrations


RULE_KIND = "parser_atomizer_term"
ATOMIZER_TERMS = (
    "atomiser",
    "atomizer",
    "\u0430\u0442\u043e\u043c\u0430\u0439\u0437\u0435\u0440",
)


def seed_terms(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for term in ATOMIZER_TERMS:
        GlobalRule.objects.update_or_create(
            rule_kind=RULE_KIND,
            scope_type="global",
            rule_text=term,
            defaults={
                "title": f"Atomizer term: {term}",
                "scope_value": "",
                "priority": 35,
                "confidence": 100,
                "active": True,
                "approved": True,
            },
        )


def unseed_terms(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text__in=ATOMIZER_TERMS,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0017_seed_vintage_parser_terms"),
    ]

    operations = [
        migrations.RunPython(seed_terms, unseed_terms),
    ]
