from django.db import migrations


RULE_KIND = "parser_dented_packaging_term"
DENTED_PACKAGING_TERMS = (
    "\u0431\u0435\u0437 \u0446\u0435\u043b",
    "\u0431\u0435\u0437 \u0446\u0435\u043b\u043b",
    "\u0431\u0435\u0437 \u0446\u0435\u043b\u043e\u0444\u0430\u043d\u0430",
    "\u0431\u0435\u0437 \u0446\u0435\u043b\u043b\u043e\u0444\u0430\u043d\u0430",
    "\u0431\u0435\u0437 \u0446\u0435\u043b\u043e\u0444\u043d\u0430",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d\u0430 \u0441\u043b\u044e\u0434\u0430",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d\u043d\u0430\u044f \u0441\u043b\u044e\u0434\u0430",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d\u0440 \u0441\u043b\u044e\u0434\u0430",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d\u0430",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d\u043d\u0430\u044f",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d \u0446\u0435\u043b\u043e\u0444\u0430\u043d",
    "\u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d \u0446\u0435\u043b\u043b\u043e\u0444\u0430\u043d",
    "\u043f\u043e\u0440\u0432\u0430\u043d\u0430 \u0441\u043b\u044e\u0434\u0430",
    "\u043f\u043e\u0440\u0432\u0430\u043d\u043d\u0430\u044f \u0441\u043b\u044e\u0434\u0430",
    "\u043c\u044f\u0442\u0430\u044f \u0441\u043b\u044e\u0434\u0430",
    "\u043f\u043e\u043c\u044f\u0442\u0430\u044f \u0441\u043b\u044e\u0434\u0430",
)


def seed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for term in DENTED_PACKAGING_TERMS:
        GlobalRule.objects.update_or_create(
            rule_kind=RULE_KIND,
            scope_type="global",
            rule_text=term,
            defaults={
                "title": f"Dented packaging term: {term}",
                "scope_value": "",
                "priority": 40,
                "approved": True,
                "active": True,
            },
        )


def unseed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    GlobalRule.objects.filter(rule_kind=RULE_KIND, scope_type="global", rule_text__in=DENTED_PACKAGING_TERMS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0009_seed_exclus_edition_preprocess_rule"),
    ]

    operations = [
        migrations.RunPython(seed_rules, unseed_rules),
    ]
