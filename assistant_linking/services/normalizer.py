from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from assistant_linking.models import (
    CONCENTRATION_ALIAS_CACHE_KEY,
    BrandAlias,
    ConcentrationAlias,
    ParsedSupplierProduct,
    ProductAlias,
)
from assistant_linking.services.garbage import GARBAGE_MODIFIER, GARBAGE_WARNING_PREFIX, match_garbage_keyword
from catalog.models import Brand
from prices.models import SupplierProduct


PARSER_VERSION = "deterministic-v1"

DEFAULT_CONCENTRATION_ALIASES = (
    ("extrait de parfum", "Extrait de Parfum"),
    ("extrait", "Extrait de Parfum"),
    ("eau de parfum", "Eau de Parfum"),
    ("edp", "Eau de Parfum"),
    ("eau de toilette", "Eau de Toilette"),
    ("edt", "Eau de Toilette"),
    ("eau de cologne", "Eau de Cologne"),
    ("edc", "Eau de Cologne"),
    ("parfum", "Parfum"),
    ("perfume oil", "Perfume Oil"),
)

GENDER_ALIASES = (
    (r"\bpour homme\b|\bhomme\b|\buomo\b|\bmen\b|\bman\b", "men"),
    (r"\bpour femme\b|\bfemme\b|\bdonna\b|\bwomen\b|\bwoman\b|\blady\b", "women"),
    (r"\bunisex\b|\bунисекс\b|\bуни\b", "unisex"),
)

MODIFIER_TERMS = ("intense", "elixir", "absolu", "eau intense", "extreme", "sport", "fraiche", "fraicheur")
TESTER_TERMS = ("tester", "test", "тестер", "тест")
SAMPLE_TERMS = ("sample", "пробник", "vial")
TRAVEL_TERMS = ("travel", "мини", "mini")
SET_TERMS = ("set", "набор", "coffret")
NO_BOX_TERMS = ("no box", "without box", "без короб")
GENDER_TERMS = ("pour homme", "homme", "uomo", "men", "man", "pour femme", "femme", "donna", "women", "woman", "lady", "unisex", "унисекс", "уни")


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
    text = re.sub(r"\b(edp|edt|edc)(?=\d)", r"\1 ", text)
    text = re.sub(r"(?<=\d)(edp|edt|edc)\b", r" \1", text)
    text = re.sub(r"\b(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)(?=\d)", r"\1 ", text)
    text = re.sub(r"(?<=\d)(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)\b", r" \1", text)
    text = re.sub(r"[\u00a0_\\/,;:|()\[\]{}+]+", " ", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_concentration_alias_rows():
    rows = cache.get(CONCENTRATION_ALIAS_CACHE_KEY)
    if rows is not None:
        return rows
    rows = list(
        ConcentrationAlias.objects.filter(active=True)
        .order_by("supplier__name", "priority", "alias_text")
        .values_list("supplier_id", "normalized_alias", "concentration", "is_regex")
    )
    if not rows:
        rows = [(None, normalize_text(alias_text), concentration, False) for alias_text, concentration in DEFAULT_CONCENTRATION_ALIASES]
    cache.set(CONCENTRATION_ALIAS_CACHE_KEY, rows, 300)
    return rows


def _split_terms(value: str) -> list[str]:
    return [normalize_text(term) for term in re.split(r"[,;\n]+", value or "") if normalize_text(term)]


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(^|\s){re.escape(phrase)}($|\s)", text))


def _contains_any_phrase(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_phrase(text, term) for term in terms)


def _strip_known_terms(text: str, terms: list[str]) -> str:
    remaining = text
    for term in [normalize_text(term) for term in terms if term]:
        remaining = re.sub(rf"(^|\s){re.escape(term)}($|\s)", " ", remaining)
    return re.sub(r"\s+", " ", remaining).strip()


def _extract_size(text: str) -> tuple[Decimal | None, str, str]:
    ml_match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:ml|мл|м\.л\.?)(?=\s|$)", text)
    if ml_match:
        raw = ml_match.group(0)
        value = Decimal(ml_match.group(1).replace(",", ".")).quantize(Decimal("0.01"))
        return value, raw, text.replace(raw, " ")
    reversed_ml_match = re.search(r"\b(?:ml|Ð¼Ð»|Ð¼\.Ð»\.?)\s*(\d+(?:[.,]\d+)?)(?=\s|$)", text)
    if reversed_ml_match:
        raw = reversed_ml_match.group(0)
        value = Decimal(reversed_ml_match.group(1).replace(",", ".")).quantize(Decimal("0.01"))
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


def _extract_loose_trailing_size(text: str) -> tuple[Decimal | None, str, str]:
    trailing_terms = (
        "tester",
        "test",
        "sample",
        "travel",
        "set",
        "mini",
        "Ñ‚ÐµÑÑ‚ÐµÑ€",
        "Ñ‚ÐµÑÑ‚",
        "Ð¿Ñ€Ð¾Ð±Ð½Ð¸Ðº",
        "Ð¼Ð¸Ð½Ð¸",
        "Ð½Ð°Ð±Ð¾Ñ€",
    )
    trailing_pattern = "|".join(re.escape(term) for term in trailing_terms)
    match = re.search(
        rf"(?P<prefix>.*?)(?:^|\s)(?P<size>\d+(?:[.,]\d+)?)(?:\s+(?:{trailing_pattern}))*\s*$",
        text,
    )
    if not match:
        return None, "", text
    prefix = (match.group("prefix") or "").strip()
    if prefix.endswith((" no", " number")):
        return None, "", text
    raw = match.group("size")
    try:
        value = Decimal(raw.replace(",", ".")).quantize(Decimal("0.01"))
    except Exception:
        return None, "", text
    if value < Decimal("7"):
        return None, "", text
    return value, raw, prefix


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

    garbage_keyword = match_garbage_keyword(text)
    if garbage_keyword:
        result.modifiers = [GARBAGE_MODIFIER]
        result.warnings = [f"{GARBAGE_WARNING_PREFIX}: {garbage_keyword}"]
        result.confidence = 100
        return result

    size, raw_size, text = _extract_size(text)
    result.size_ml = size
    result.raw_size_text = raw_size

    concentration_alias_rows = get_concentration_alias_rows()
    supplier_aliases = [row for row in concentration_alias_rows if row[0] == product.supplier_id]
    global_aliases = [row for row in concentration_alias_rows if row[0] is None]
    for _, needle, value, is_regex in supplier_aliases + global_aliases:
        if is_regex:
            matched = re.search(needle, text)
            if not matched:
                continue
            result.concentration = value
            text = re.sub(needle, " ", text).strip()
            break
        if re.search(rf"(^|\s){re.escape(needle)}($|\s)", text):
            result.concentration = value
            text = re.sub(rf"(^|\s){re.escape(needle)}($|\s)", " ", text).strip()
            break

    for pattern, value in GENDER_ALIASES:
        if re.search(pattern, text):
            result.supplier_gender_hint = value
            break

    result.is_tester = _contains_any_phrase(text, TESTER_TERMS)
    result.is_sample = _contains_any_phrase(text, SAMPLE_TERMS)
    result.is_travel = _contains_any_phrase(text, TRAVEL_TERMS)
    result.is_set = _contains_any_phrase(text, SET_TERMS)
    result.packaging = "no_box" if _contains_any_phrase(text, NO_BOX_TERMS) else ""
    result.variant_type = "sample" if result.is_sample else ("travel" if result.is_travel else ("set" if result.is_set else ("tester" if result.is_tester else "standard")))
    result.modifiers = [term for term in MODIFIER_TERMS if re.search(rf"(^|\s){re.escape(term)}($|\s)", text)]

    alias, brand = _match_aliases(text, product.supplier_id)
    if brand:
        result.normalized_brand = brand
        result.detected_brand_text = alias.alias_text if alias else brand.name
        brand_text = normalize_text(result.detected_brand_text)
        text = re.sub(rf"(^|\s){re.escape(brand_text)}($|\s)", " ", text).strip()
        if alias and alias.supplier_id:
            result.warnings.append("supplier-specific alias overrode global alias")

    if not result.size_ml:
        size, raw_size, compact_text = _extract_loose_trailing_size(text)
        if size is not None:
            result.size_ml = size
            result.raw_size_text = raw_size
            text = compact_text

    product_aliases = ProductAlias.objects.filter(active=True).order_by("supplier_id", "priority", "-alias_text")
    if result.normalized_brand:
        product_aliases = product_aliases.filter(Q(brand_id=result.normalized_brand.id) | Q(brand__isnull=True))
    for product_alias in list(product_aliases.filter(supplier_id=product.supplier_id)) + list(product_aliases.filter(supplier__isnull=True)):
        alias_text = normalize_text(product_alias.alias_text)
        excluded_terms = _split_terms(product_alias.excluded_terms)
        if alias_text and _contains_phrase(text, alias_text) and not any(_contains_phrase(text, term) for term in excluded_terms):
            result.product_name_text = product_alias.canonical_text
            if product_alias.concentration:
                result.concentration = product_alias.concentration
            if product_alias.audience:
                result.supplier_gender_hint = product_alias.audience
            break

    # A confirmed catalogue link is stronger than supplier text and should
    # keep reparses aligned with the canonical catalogue identity.
    if product.catalog_perfume_id:
        perfume = product.catalog_perfume
        result.normalized_brand = perfume.brand
        result.detected_brand_text = perfume.brand.name
        result.product_name_text = perfume.name
        if perfume.concentration:
            result.concentration = perfume.concentration
        if perfume.audience:
            result.supplier_gender_hint = perfume.audience
        if product.catalog_variant_id:
            variant = product.catalog_variant
            if variant.size_ml:
                result.size_ml = variant.size_ml
            result.packaging = variant.packaging or ""
            result.variant_type = variant.variant_type or "standard"
            result.is_tester = variant.is_tester
            result.is_sample = result.variant_type == "sample"
            result.is_travel = result.variant_type == "travel"
            result.is_set = result.variant_type == "set"

    if not result.product_name_text:
        remaining = text
        remaining = _strip_known_terms(
            remaining,
            [
                result.raw_size_text,
                result.concentration,
                *GENDER_TERMS,
                *TESTER_TERMS,
                *SAMPLE_TERMS,
                *TRAVEL_TERMS,
                *SET_TERMS,
                *NO_BOX_TERMS,
            ],
        )
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
