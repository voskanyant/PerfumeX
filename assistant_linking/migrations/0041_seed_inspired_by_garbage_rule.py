from django.db import migrations
from django.utils import timezone


RULE_KIND = "garbage_keyword"
RULE_TEXT = "\u043f\u043e \u043c\u043e\u0442\u0438\u0432\u0430\u043c"
RULE_TITLE = "Garbage keyword: inspired-by imitation rows"
PARSER_VERSION = "deterministic-v31"


def _normalized_text(product) -> str:
    return " ".join(
        part.strip().casefold()
        for part in (product.brand or "", product.name or "", product.size or "")
        if part and part.strip()
    )


def seed_rule_and_update_rows(apps, schema_editor):
    GlobalRule = apps.get_model("assistant_core", "GlobalRule")
    SupplierProduct = apps.get_model("prices", "SupplierProduct")
    ParsedSupplierProduct = apps.get_model("assistant_linking", "ParsedSupplierProduct")
    NormalizationStatsSnapshot = apps.get_model(
        "assistant_linking", "NormalizationStatsSnapshot"
    )

    GlobalRule.objects.update_or_create(
        rule_kind=RULE_KIND,
        scope_type="global",
        rule_text=RULE_TEXT,
        defaults={
            "title": RULE_TITLE,
            "scope_value": "",
            "priority": 10,
            "confidence": 100,
            "active": True,
            "approved": True,
        },
    )

    now = timezone.now()
    warning = f"excluded garbage keyword: {RULE_TEXT}"
    products = SupplierProduct.objects.filter(name__icontains=RULE_TEXT).select_related(
        "assistant_parse"
    )
    for product in products.iterator():
        existing_parse = getattr(product, "assistant_parse", None)
        if existing_parse and existing_parse.locked_by_human:
            continue
        ParsedSupplierProduct.objects.update_or_create(
            supplier_product=product,
            defaults={
                "raw_name": product.name or "",
                "normalized_text": _normalized_text(product),
                "detected_brand_text": "",
                "normalized_brand_id": None,
                "product_name_text": "",
                "collection_name": "",
                "concentration": "",
                "size_ml": None,
                "raw_size_text": "",
                "release_year": None,
                "supplier_gender_hint": "",
                "packaging": "",
                "variant_type": "",
                "is_tester": False,
                "is_sample": False,
                "is_travel": False,
                "is_set": False,
                "modifiers": ["garbage"],
                "warnings": [warning],
                "confidence": 100,
                "parser_version": PARSER_VERSION,
                "last_parsed_at": now,
            },
        )

    NormalizationStatsSnapshot.objects.update(is_stale=True)


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
        ("assistant_linking", "0040_seed_boss_baldessarini_brand_alias"),
        ("assistant_core", "0013_seed_limited_ed_preprocess_rule"),
    ]

    operations = [
        migrations.RunPython(seed_rule_and_update_rows, unseed_rule),
    ]
