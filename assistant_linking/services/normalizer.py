from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from assistant_linking.models import BrandAlias, ParsedSupplierProduct, ProductAlias
from catalog.models import Brand
from prices.models import SupplierProduct


PARSER_VERSION = "deterministic-v1"

CONCENTRATION_ALIASES = (
    ("extrait de parfum", "extrait"),
    ("extrait", "extrait"),
    ("parfum", "parfum"),
    ("eau de parfum", "edp"),
    ("edp", "edp"),
    ("eau de toilette", "edt"),
    ("edt", "edt"),
    ("eau de cologne", "edc"),
    ("edc", "edc"),
)

GENDER_ALIASES = (
    (r"\bpour homme\b|\bhomme\b|\buomo\b|\bmen\b|\bman\b", "men"),
    (r"\bpour femme\b|\bfemme\b|\bdonna\b|\bwomen\b|\bwoman\b|\blady\b", "women"),
    (r"\bunisex\b", "unisex"),
)

MODIFIER_TERMS = ("intense", "elixir", "absolu", "eau intense", "extreme", "sport", "fraiche", "fraicheur")


@dataclass
class ParseResult:
    raw_name: str
    normalized_text: str
    detected_brand_text: str = ""
    normalized_brand: Brand | None = None
    product_name_text: str = ""
    concentration: str = ""
    size_ml: Decimal | None = None
    raw_size_text: str = ""
    supplier_gender_hint: str = ""
    packaging: str = ""
    variant_type: str = ""
    is_tester: bool = False
    is_sample: bool = False
    is_travel: bool = False
    is_set: bool = False
    modifiers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: int = 0


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[\u00a0_/,;:|()\[\]{}]+", " ", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_size(text: str) -> tuple[Decimal | None, str, str]:
    ml_match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:ml|мл)\b", text)
    if ml_match:
        raw = ml_match.group(0)
        value = Decimal(ml_match.group(1).replace(",", ".")).quantize(Decimal("0.01"))
        return value, raw, text.replace(raw, " ")
    oz_match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:fl\s*)?oz\b", text)
    if oz_match:
        raw = oz_match.group(0)
        oz = Decimal(oz_match.group(1).replace(",", "."))
        common = {
            Decimal("0.34"): Decimal("10.00"),
            Decimal("1.00"): Decimal("30.00"),
            Decimal("1.70"): Decimal("50.00"),
            Decimal("2.50"): Decimal("75.00"),
            Decimal("3.30"): Decimal("100.00"),
            Decimal("3.40"): Decimal("100.00"),
        }
        if oz.quantize(Decimal("0.01")) in common:
            return common[oz.quantize(Decimal("0.01"))], raw, text.replace(raw, " ")
        value = (oz * Decimal("29.5735")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return value.quantize(Decimal("0.01")), raw, text.replace(raw, " ")
    return None, "", text


def _match_aliases(text: str, supplier_id: int | None):
    aliases = BrandAlias.objects.filter(active=True).select_related("brand").order_by("supplier_id", "priority", "-normalized_alias")
    supplier_aliases = [alias for alias in aliases if alias.supplier_id == supplier_id]
    global_aliases = [alias for alias in aliases if alias.supplier_id is None]
    for alias in supplier_aliases + global_aliases:
        pattern = alias.normalized_alias or normalize_text(alias.alias_text)
        if alias.is_regex:
            if re.search(pattern, text):
                return alias, alias.brand
        elif re.search(rf"(^|\s){re.escape(pattern)}($|\s)", text):
            return alias, alias.brand
    for brand in Brand.objects.filter(is_active=True).order_by("-name"):
        brand_text = normalize_text(brand.name)
        if re.search(rf"(^|\s){re.escape(brand_text)}($|\s)", text):
            return None, brand
    return None, None


def parse_supplier_product(product: SupplierProduct) -> ParseResult:
    raw = product.name or ""
    text = normalize_text(" ".join([product.brand or "", raw, product.size or ""]))
    result = ParseResult(raw_name=raw, normalized_text=text)

    size, raw_size, text = _extract_size(text)
    result.size_ml = size
    result.raw_size_text = raw_size

    for needle, value in CONCENTRATION_ALIASES:
        if re.search(rf"(^|\s){re.escape(needle)}($|\s)", text):
            result.concentration = value
            text = re.sub(rf"(^|\s){re.escape(needle)}($|\s)", " ", text).strip()
            break

    for pattern, value in GENDER_ALIASES:
        if re.search(pattern, text):
            result.supplier_gender_hint = value
            break

    result.is_tester = bool(re.search(r"\btester\b|\bтестер\b|\btest\b", text))
    result.is_sample = bool(re.search(r"\bsample\b|\bпробник\b|\bvial\b", text))
    result.is_travel = bool(re.search(r"\btravel\b|\bмини\b|\bmini\b", text))
    result.is_set = bool(re.search(r"\bset\b|\bнабор\b|\bcoffret\b", text))
    result.packaging = "no_box" if re.search(r"\bno box\b|\bwithout box\b|\bбез короб", text) else ""
    result.variant_type = "sample" if result.is_sample else ("travel" if result.is_travel else ("set" if result.is_set else "standard"))
    result.modifiers = [term for term in MODIFIER_TERMS if re.search(rf"(^|\s){re.escape(term)}($|\s)", text)]

    alias, brand = _match_aliases(text, product.supplier_id)
    if brand:
        result.normalized_brand = brand
        result.detected_brand_text = alias.alias_text if alias else brand.name
        brand_text = normalize_text(result.detected_brand_text)
        text = re.sub(rf"(^|\s){re.escape(brand_text)}($|\s)", " ", text).strip()
        if alias and alias.supplier_id:
            result.warnings.append("supplier-specific alias overrode global alias")

    product_aliases = ProductAlias.objects.filter(active=True).order_by("supplier_id", "priority", "-alias_text")
    for product_alias in list(product_aliases.filter(supplier_id=product.supplier_id)) + list(product_aliases.filter(supplier__isnull=True)):
        alias_text = normalize_text(product_alias.alias_text)
        if alias_text and alias_text in text:
            result.product_name_text = product_alias.canonical_text
            if product_alias.concentration and not result.concentration:
                result.concentration = product_alias.concentration
            if product_alias.audience and not result.supplier_gender_hint:
                result.supplier_gender_hint = product_alias.audience
            break

    if not result.product_name_text:
        remaining = text
        for term in [result.raw_size_text, result.concentration, "tester", "sample", "travel", "set", "no box", "without box"]:
            if term:
                remaining = remaining.replace(term, " ")
        result.product_name_text = re.sub(r"\s+", " ", remaining).strip()[:255]

    if not result.normalized_brand:
        result.warnings.append("brand missing")
    if not result.product_name_text:
        result.warnings.append("product name missing")
    if not result.concentration:
        result.warnings.append("concentration missing")
    if not result.size_ml:
        result.warnings.append("size ambiguous")
    if not result.supplier_gender_hint:
        result.warnings.append("gender missing")
    for modifier in result.modifiers:
        result.warnings.append(f"{modifier} detected")

    score = 25
    score += 25 if result.normalized_brand else 0
    score += 15 if result.product_name_text else 0
    score += 15 if result.concentration else 0
    score += 15 if result.size_ml else 0
    score += 5 if result.supplier_gender_hint else 0
    result.confidence = min(score, 100)
    result.normalized_text = normalize_text(" ".join([result.normalized_text, result.product_name_text]))
    return result


def save_parse(product: SupplierProduct, *, force: bool = False) -> ParsedSupplierProduct:
    existing = getattr(product, "assistant_parse", None)
    if existing and existing.locked_by_human and not force:
        return existing
    parsed = parse_supplier_product(product)
    obj, _ = ParsedSupplierProduct.objects.update_or_create(
        supplier_product=product,
        defaults={
            "raw_name": parsed.raw_name,
            "normalized_text": parsed.normalized_text,
            "detected_brand_text": parsed.detected_brand_text,
            "normalized_brand": parsed.normalized_brand,
            "product_name_text": parsed.product_name_text,
            "concentration": parsed.concentration,
            "size_ml": parsed.size_ml,
            "raw_size_text": parsed.raw_size_text,
            "supplier_gender_hint": parsed.supplier_gender_hint,
            "packaging": parsed.packaging,
            "variant_type": parsed.variant_type,
            "is_tester": parsed.is_tester,
            "is_sample": parsed.is_sample,
            "is_travel": parsed.is_travel,
            "is_set": parsed.is_set,
            "modifiers": parsed.modifiers,
            "warnings": parsed.warnings,
            "confidence": parsed.confidence,
            "parser_version": PARSER_VERSION,
            "last_parsed_at": timezone.now(),
        },
    )
    return obj
