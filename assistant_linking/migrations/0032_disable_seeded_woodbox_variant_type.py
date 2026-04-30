from django.db import migrations


RULE_KIND = "parser_variant_type_term"
RULE_TEXT = "woodbox => woodbox"
RULE_TITLE = "Variant type: woodbox"


def disable_seeded_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        title=RULE_TITLE,
    ).update(active=False)


def enable_seeded_rule(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        title=RULE_TITLE,
    ).update(active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0031_merge_0024_seed_woodbox_variant_type_0030_seed_salvador_dali_brand_aliases"),
    ]

    operations = [
        migrations.RunPython(disable_seeded_rule, enable_seeded_rule),
    ]
