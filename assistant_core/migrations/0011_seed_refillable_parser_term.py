from django.db import migrations


RULE_KIND = "parser_refillable_packaging_term"
REFILLABLE_PACKAGING_TERMS = (
    "refillable",
)


def seed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for term in REFILLABLE_PACKAGING_TERMS:
        GlobalRule.objects.update_or_create(
            rule_kind=RULE_KIND,
            scope_type="global",
            rule_text=term,
            defaults={
                "title": f"Refillable packaging term: {term}",
                "scope_value": "",
                "priority": 50,
                "approved": True,
                "active": True,
            },
        )


def unseed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(rule_kind=RULE_KIND, scope_type="global", rule_text__in=REFILLABLE_PACKAGING_TERMS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0010_seed_dented_packaging_terms"),
    ]

    operations = [
        migrations.RunPython(seed_rules, unseed_rules),
    ]
