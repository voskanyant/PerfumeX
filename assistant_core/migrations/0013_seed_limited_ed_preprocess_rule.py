from django.db import migrations


RULE_KIND = "regex_preprocess"
RULE_TEXT = r"\blimited\s+ed\.?\b => limited edition"
RULE_TITLE = "Normalize Limited Ed supplier abbreviation"


def seed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.update_or_create(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        defaults={
            "title": RULE_TITLE,
            "scope_value": "",
            "priority": 20,
            "approved": True,
            "active": True,
        },
    )


def unseed_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(rule_kind=RULE_KIND, scope_type="global", rule_text=RULE_TEXT).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0012_seed_packaging_abbreviation_parser_terms"),
    ]

    operations = [
        migrations.RunPython(seed_rule, unseed_rule),
    ]
