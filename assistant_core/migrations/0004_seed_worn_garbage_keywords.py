from django.db import migrations


WORN_GARBAGE_KEYWORDS = (
    "\u043f\u043e\u0442\u0435\u0440\u0442",
    "\u043f\u043e\u0442\u0451\u0440\u0442",
)


def seed_worn_keywords(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for keyword in WORN_GARBAGE_KEYWORDS:
        GlobalRule.objects.update_or_create(
            rule_kind="garbage_keyword",
            scope_type="global",
            rule_text=keyword,
            defaults={
                "title": f"Garbage keyword: {keyword}",
                "scope_value": "",
                "priority": 10,
                "confidence": 100,
                "active": True,
                "approved": True,
            },
        )


def unseed_worn_keywords(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for keyword in WORN_GARBAGE_KEYWORDS:
        GlobalRule.objects.filter(rule_kind="garbage_keyword", scope_type="global", rule_text=keyword).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0003_seed_audience_parser_rules"),
    ]

    operations = [
        migrations.RunPython(seed_worn_keywords, unseed_worn_keywords),
    ]
