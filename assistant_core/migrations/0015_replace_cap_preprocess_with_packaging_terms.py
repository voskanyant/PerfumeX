from __future__ import annotations

from django.db import migrations


OLD_RULE_KIND = "regex_preprocess"
OLD_RULE_TEXT = (
    r"\b(?:с|без)\s+крышк(?:ой|и|а|у)?\b|\b(?:with|without)\s+(?:cap|lid)\b => "
)
WITH_CAP_RULE_KIND = "parser_with_cap_packaging_term"
WITH_CAP_TERMS = (
    "с крышкой",
    "с крышк",
    "with cap",
    "with lid",
)


def seed_terms(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(
        rule_kind=OLD_RULE_KIND,
        scope_type="global",
        rule_text=OLD_RULE_TEXT,
    ).update(active=False)
    for term in WITH_CAP_TERMS:
        GlobalRule.objects.update_or_create(
            rule_kind=WITH_CAP_RULE_KIND,
            scope_type="global",
            rule_text=term,
            defaults={
                "title": f"With-cap packaging term: {term}",
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
        rule_kind=WITH_CAP_RULE_KIND,
        scope_type="global",
        rule_text__in=WITH_CAP_TERMS,
    ).delete()
    GlobalRule.objects.filter(
        rule_kind=OLD_RULE_KIND,
        scope_type="global",
        rule_text=OLD_RULE_TEXT,
    ).update(active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0014_seed_bracketed_l_audience"),
        ("assistant_linking", "0018_seed_cap_preprocess_rule"),
    ]

    operations = [
        migrations.RunPython(seed_terms, unseed_terms),
    ]
