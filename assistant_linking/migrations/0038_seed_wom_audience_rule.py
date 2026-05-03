from django.db import migrations


RULE_KIND = "parser_audience_term"
RULE_TEXT = "wom => Woman | women"
RULE_TITLE = "Audience alias: wom means woman"


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
        ("assistant_linking", "0037_seed_supplier_white_comment_rule"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
