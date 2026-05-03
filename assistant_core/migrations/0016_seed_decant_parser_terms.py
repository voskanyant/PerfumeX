from __future__ import annotations

from django.db import migrations


RULE_KIND = "parser_decant_term"
DECANT_TERMS = (
    "\u043e\u0442\u043b\u0438\u0432 \u0438\u0437 \u0444\u043b\u0430\u043a\u043e\u043d\u0430",
    "\u043e\u0442\u043b\u0438\u0432\u0430\u043d\u0442",
    "\u043e\u0442\u043b\u0438\u0432\u0430",
    "\u043e\u0442\u043b\u0438\u0432\u0430\u043d",
    "\u043e\u0442\u043b\u0438\u0432",
    "\u043e\u0442\u043b",
)


def seed_terms(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for term in DECANT_TERMS:
        GlobalRule.objects.update_or_create(
            rule_kind=RULE_KIND,
            scope_type="global",
            rule_text=term,
            defaults={
                "title": f"Decant term: {term}",
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
        rule_text__in=DECANT_TERMS,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0015_replace_cap_preprocess_with_packaging_terms"),
    ]

    operations = [
        migrations.RunPython(seed_terms, unseed_terms),
    ]
