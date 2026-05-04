from django.db import migrations


GLASS_TERM = "\u0441\u0442\u0435\u043a\u043b\u043e"


def seed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    rules = [
        (
            "parser_no_box_packaging_term",
            "No-box packaging term: glass",
            GLASS_TERM,
            "Marks supplier glass/no-box notes as No Box packaging.",
        ),
        (
            "parser_tester_term",
            "Tester term: glass",
            GLASS_TERM,
            "Marks supplier glass/no-box rows as tester bottles.",
        ),
    ]
    for rule_kind, title, rule_text, description in rules:
        GlobalRule.objects.update_or_create(
            rule_kind=rule_kind,
            scope_type="global",
            rule_text=rule_text,
            defaults={
                "title": title,
                "scope_value": "",
                "examples_json": [
                    "\u0411\u0440\u0435\u043d\u0434 Scent edp 100ml \u0441\u0442\u0435\u043a\u043b\u043e",
                    description,
                ],
                "approved": True,
                "active": True,
                "priority": 45,
                "confidence": 95,
            },
        )


def unseed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(
        rule_kind__in=["parser_no_box_packaging_term", "parser_tester_term"],
        scope_type="global",
        rule_text=GLASS_TERM,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0045_normalizationstatssnapshot_vintage_count"),
    ]

    operations = [
        migrations.RunPython(seed_rules, unseed_rules),
    ]
