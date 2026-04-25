from __future__ import annotations

from django.db import migrations


PARSER_RULES = (
    ("parser_tester_term", "tectep", 50),
    ("parser_tester_term", "<tst>", 50),
    ("parser_tester_term", "tst", 50),
    ("parser_tester_term", "тст", 50),
    ("parser_set_term", "gift set", 50),
    ("parser_set_term", "подарочный набор", 50),
    ("parser_set_term", "подарочный", 60),
    ("parser_travel_term", "дорожн", 50),
    ("parser_mini_term", "миниатюра", 50),
    ("parser_refill_term", "refill", 50),
    ("parser_refill_term", "refil", 50),
    ("parser_refill_term", "рефил", 50),
    ("parser_refill_term", "сменный блок", 50),
    ("parser_refill_term", "сменный", 60),
    ("parser_refill_term", "запасной", 50),
    ("parser_refill_term", "сменных блоков", 50),
    ("regex_preprocess", r"\beau de perfume\b => eau de parfum", 10),
    ("regex_preprocess", r"\beau de parfume\b => eau de parfum", 10),
    ("regex_preprocess", r"\beau de parf\b(?!um) => eau de parfum", 10),
    ("regex_preprocess", r"(\d+)\.0\s*(?=мл|ml) => \1 ", 20),
    ("regex_preprocess", r"(\d+)\s*мл\.? => \1 ml", 20),
    ("regex_preprocess", r"\b(edp|edt|edc)(\d) => \1 \2", 20),
)

GARBAGE_KEYWORDS = (
    "подмят",
    "подмятый",
    "помят",
    "помятый",
    "поврежд",
    "fake",
    "old design",
    "old box",
    "vintage",
)


def seed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for rule_kind, rule_text, priority in PARSER_RULES:
        GlobalRule.objects.update_or_create(
            rule_kind=rule_kind,
            scope_type="global",
            rule_text=rule_text,
            defaults={
                "title": f"{rule_kind}: {rule_text}",
                "scope_value": "",
                "priority": priority,
                "confidence": 100,
                "active": True,
                "approved": True,
            },
        )
    for keyword in GARBAGE_KEYWORDS:
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


def unseed_rules(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    for rule_kind, rule_text, _priority in PARSER_RULES:
        GlobalRule.objects.filter(rule_kind=rule_kind, scope_type="global", rule_text=rule_text).delete()
    for keyword in GARBAGE_KEYWORDS:
        GlobalRule.objects.filter(rule_kind="garbage_keyword", scope_type="global", rule_text=keyword).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_rules, unseed_rules),
    ]
