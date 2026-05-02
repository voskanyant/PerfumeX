from django.db import migrations


PACKAGING_TERMS = (
    (
        "parser_with_cap_packaging_term",
        "With-cap packaging term",
        (
            "c \u0444\u0438\u0440\u043c \u043a\u0440",
            "c \u0444\u0438\u0440\u043c. \u043a\u0440",
            "c \u0444\u0438\u0440\u043c.\u043a\u0440",
            "c \u0444\u0438\u0440\u043c \u043a\u0440\u044b\u0448",
            "c \u0444\u0438\u0440\u043c. \u043a\u0440\u044b\u0448",
            "\u0441 \u0444\u0438\u0440\u043c \u043a\u0440",
            "\u0441 \u0444\u0438\u0440\u043c. \u043a\u0440",
            "\u0441 \u0444\u0438\u0440\u043c.\u043a\u0440",
            "\u0441 \u0444\u0438\u0440\u043c \u043a\u0440\u044b\u0448",
            "\u0441 \u0444\u0438\u0440\u043c. \u043a\u0440\u044b\u0448",
        ),
    ),
    (
        "parser_old_design_packaging_term",
        "Old-design packaging term",
        (
            "\u0441\u0442.\u0434\u0438",
            "\u0441\u0442.\u0434\u0438\u0437",
            "\u0441\u0442.\u0434\u0438\u0437\u0430\u0439\u043d",
            "\u0441\u0442 \u0434\u0438",
        ),
    ),
)


def seed_packaging_terms(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for rule_kind, title_prefix, terms in PACKAGING_TERMS:
        for term in terms:
            GlobalRule.objects.update_or_create(
                rule_kind=rule_kind,
                scope_type="global",
                rule_text=term,
                defaults={
                    "title": f"{title_prefix}: {term}",
                    "priority": 45,
                    "approved": True,
                    "active": True,
                },
            )


def unseed_packaging_terms(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for rule_kind, _title_prefix, terms in PACKAGING_TERMS:
        GlobalRule.objects.filter(rule_kind=rule_kind, scope_type="global", rule_text__in=terms).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0011_seed_refillable_parser_term"),
    ]

    operations = [
        migrations.RunPython(seed_packaging_terms, unseed_packaging_terms),
    ]
