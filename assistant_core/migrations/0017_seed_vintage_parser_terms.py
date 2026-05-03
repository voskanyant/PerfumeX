from __future__ import annotations

from django.db import migrations


RULE_KIND = "parser_vintage_term"
VINTAGE_TERMS = (
    "\u0432\u0438\u043d\u0442\u0430\u0436",
    "\u0432\u0438\u043d\u0442\u0430\u0436\u043d\u044b\u0439",
    "\u0432\u0438\u043d\u0442\u0430\u0436\u043d\u0430\u044f",
    "\u0432\u0438\u043d\u0442",
    "vintage",
    "vint",
)


def seed_terms(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for term in VINTAGE_TERMS:
        GlobalRule.objects.update_or_create(
            rule_kind=RULE_KIND,
            scope_type="global",
            rule_text=term,
            defaults={
                "title": f"Vintage term: {term}",
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
        rule_text__in=VINTAGE_TERMS,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0016_seed_decant_parser_terms"),
    ]

    operations = [
        migrations.RunPython(seed_terms, unseed_terms),
    ]
