from django.db import migrations


RULE_KIND = "parser_supplier_comment_term"
RULE_TEXT = "\u0431\u0435\u043b\u044b\u0439, \u0431\u0435\u043b\u0430\u044f, \u0431\u0435\u043b\u043e\u0435, \u0431\u0435\u043b"
RULE_TITLE = "Supplier comment terms: white"


def seed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.update_or_create(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        defaults={
            "title": RULE_TITLE,
            "scope_value": "",
            "approved": True,
            "active": True,
            "priority": 50,
        },
    )


def unseed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        title=RULE_TITLE,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0036_seed_alexandre_j_ultimate_crystal_aliases"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
