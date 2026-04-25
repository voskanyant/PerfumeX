from __future__ import annotations

from django.db import migrations


AUDIENCE_RULES = (
    ("pour femme => Pour Femme | women", 40),
    ("femme => Femme | women", 45),
    ("donna => Woman | women", 50),
    ("women => Woman | women", 50),
    ("woman => Woman | women", 50),
    ("female => Woman | women", 50),
    ("lady => Woman | women", 55),
    ("her => Woman | women", 60),
    ("w => Woman | women", 80),
    ("жен => Woman | women", 50),
    ("женский => Woman | women", 50),
    ("женская => Woman | women", 50),
    ("женские => Woman | women", 50),
    ("pour homme => Pour Homme | men", 40),
    ("homme => Homme | men", 45),
    ("uomo => Men | men", 50),
    ("men => Men | men", 50),
    ("man => Men | men", 50),
    ("male => Men | men", 50),
    ("him => Men | men", 60),
    ("m => Men | men", 80),
    ("муж => Men | men", 50),
    ("мужской => Men | men", 50),
    ("мужская => Men | men", 50),
    ("мужские => Men | men", 50),
    ("unisex => Unisex | unisex", 50),
    ("унисекс => Unisex | unisex", 50),
    ("уни => Unisex | unisex", 60),
)


def seed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for rule_text, priority in AUDIENCE_RULES:
        GlobalRule.objects.update_or_create(
            rule_kind="parser_audience_term",
            scope_type="global",
            rule_text=rule_text,
            defaults={
                "title": f"Audience alias: {rule_text}",
                "scope_value": "",
                "priority": priority,
                "confidence": 100,
                "active": True,
                "approved": True,
            },
        )


def unseed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for rule_text, _priority in AUDIENCE_RULES:
        GlobalRule.objects.filter(
            rule_kind="parser_audience_term",
            scope_type="global",
            rule_text=rule_text,
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0002_seed_kb_parser_rules"),
    ]

    operations = [
        migrations.RunPython(seed_rules, unseed_rules),
    ]
