from __future__ import annotations

from django.db import migrations


SPECIFIC_PRODUCT_ALIASES = (
    "angel etoile des reves eau de nuit",
    "angel etoile des reves de nuit",
    "angel etoile des reves",
    "etoile des reves eau de nuit",
    "etoile des reves de nuit",
    "etoile des reves",
)
CANONICAL_TEXT = "Angel Etoile des Reves Eau de Nuit"
GENERIC_ALIAS_TEXT = "angel"
GENERIC_EXCLUDED_TERM = "etoile des reves"


def _append_excluded_term(value: str, term: str) -> str:
    terms = [item.strip() for item in (value or "").splitlines() if item.strip()]
    if term not in terms:
        terms.append(term)
    return "\n".join(terms)


def _remove_excluded_term(value: str, term: str) -> str:
    return "\n".join(item for item in (value or "").splitlines() if item.strip() and item.strip() != term)


def seed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    brand = Brand.objects.filter(name__in=("Thierry Mugler", "Mugler")).order_by("name").first()
    for alias_text in SPECIFIC_PRODUCT_ALIASES:
        ProductAlias.objects.update_or_create(
            alias_text=alias_text,
            supplier=None,
            defaults={
                "brand": brand,
                "canonical_text": CANONICAL_TEXT,
                "concentration": "",
                "audience": "Woman",
                "excluded_terms": "",
                "active": True,
                "priority": 20,
            },
        )

    generic_aliases = ProductAlias.objects.filter(alias_text__iexact=GENERIC_ALIAS_TEXT, supplier__isnull=True)
    if brand:
        generic_aliases = generic_aliases.filter(brand__isnull=True) | generic_aliases.filter(brand=brand)
    for alias in generic_aliases:
        updated_excluded_terms = _append_excluded_term(alias.excluded_terms, GENERIC_EXCLUDED_TERM)
        if updated_excluded_terms != alias.excluded_terms:
            alias.excluded_terms = updated_excluded_terms
            alias.save(update_fields=["excluded_terms", "updated_at"])


def unseed_aliases(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    ProductAlias = apps.get_model("assistant_linking", "ProductAlias")

    ProductAlias.objects.filter(alias_text__in=SPECIFIC_PRODUCT_ALIASES, supplier=None, canonical_text=CANONICAL_TEXT).delete()

    brand = Brand.objects.filter(name__in=("Thierry Mugler", "Mugler")).order_by("name").first()
    generic_aliases = ProductAlias.objects.filter(alias_text__iexact=GENERIC_ALIAS_TEXT, supplier__isnull=True)
    if brand:
        generic_aliases = generic_aliases.filter(brand__isnull=True) | generic_aliases.filter(brand=brand)
    for alias in generic_aliases:
        updated_excluded_terms = _remove_excluded_term(alias.excluded_terms, GENERIC_EXCLUDED_TERM)
        if updated_excluded_terms != alias.excluded_terms:
            alias.excluded_terms = updated_excluded_terms
            alias.save(update_fields=["excluded_terms", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0009_seed_hair_care_concentration_aliases"),
    ]

    operations = [
        migrations.RunPython(seed_aliases, unseed_aliases),
    ]
