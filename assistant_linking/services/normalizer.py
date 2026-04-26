from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.core.cache import cache
from django.core.mail import mail_admins
from django.db.models import Q
from django.utils import timezone
import logging
import regex

from assistant_linking.models import (
    CONCENTRATION_ALIAS_CACHE_KEY,
    BrandAlias,
    ConcentrationAlias,
    ParsedSupplierProduct,
    ProductAlias,
)
from assistant_linking.services.garbage import GARBAGE_MODIFIER, GARBAGE_WARNING_PREFIX, match_garbage_keyword
from assistant_linking.services.parser_rules import get_audience_alias_rules, get_parser_terms, get_regex_preprocess_rules
from assistant_linking.utils.text import normalize_alias_value
from catalog.models import Brand, compact_decimal_text
from prices.models import SupplierProduct


logger = logging.getLogger(__name__)
PARSER_VERSION = "deterministic-v8"
REGEX_ALIAS_TIMEOUT_SECONDS = 1.0

DEFAULT_CONCENTRATION_ALIASES = (
    ("extrait de parfum", "Extrait de Parfum"),
    ("extrait", "Extrait de Parfum"),
    ("pure perfume", "Extrait de Parfum"),
    ("perfume", "Extrait de Parfum"),
    ("parfume", "Extrait de Parfum"),
    ("parfum", "Extrait de Parfum"),
    ("духи", "Extrait de Parfum"),
    ("парфюмерная вода", "Eau de Parfum"),
    ("парфюмированная вода", "Eau de Parfum"),
    ("парфюмированная", "Eau de Parfum"),
    ("туалетная вода", "Eau de Toilette"),
    ("туалетная", "Eau de Toilette"),
    ("одеколон", "Eau de Cologne"),
    ("eau de parfum", "Eau de Parfum"),
    ("edp", "Eau de Parfum"),
    ("eau de toilette", "Eau de Toilette"),
    ("edt", "Eau de Toilette"),
    ("eau de cologne", "Eau de Cologne"),
    ("edc", "Eau de Cologne"),
    ("perfume oil", "Perfume Oil"),
    ("parfum oil", "Perfume Oil"),
    ("hair mist", "Hair Mist"),
    ("hair perfume", "Hair Perfume"),
    ("hair fragrance", "Hair Perfume"),
    ("дымка для волос", "Hair Perfume"),
    ("дымка волос", "Hair Perfume"),
    ("парфюм для волос", "Hair Perfume"),
    ("аромат для волос", "Hair Perfume"),
    ("масляные духи", "Perfume Oil"),
    ("духи масляные", "Perfume Oil"),
    ("парфюмированное масло", "Perfume Oil"),
    ("attar", "Perfume Oil"),
    ("аттар", "Perfume Oil"),
)

DEFAULT_AUDIENCE_ALIASES = (
    ("pour femme", "Pour Femme", "women"),
    ("femme", "Femme", "women"),
    ("donna", "Woman", "women"),
    ("women", "Woman", "women"),
    ("woman", "Woman", "women"),
    ("female", "Woman", "women"),
    ("lady", "Woman", "women"),
    ("her", "Woman", "women"),
    ("w", "Woman", "women"),
    ("жен", "Woman", "women"),
    ("женский", "Woman", "women"),
    ("женская", "Woman", "women"),
    ("женские", "Woman", "women"),
    ("pour homme", "Pour Homme", "men"),
    ("homme", "Homme", "men"),
    ("uomo", "Men", "men"),
    ("men", "Men", "men"),
    ("man", "Men", "men"),
    ("male", "Men", "men"),
    ("him", "Men", "men"),
    ("m", "Men", "men"),
    ("муж", "Men", "men"),
    ("мужской", "Men", "men"),
    ("мужская", "Men", "men"),
    ("мужские", "Men", "men"),
    ("unisex", "Unisex", "unisex"),
    ("унисекс", "Unisex", "unisex"),
    ("уни", "Unisex", "unisex"),
)

MODIFIER_TERMS = ("intense", "elixir", "absolu", "eau intense", "extreme", "sport", "fraiche", "fraicheur")
TESTER_TERMS = ("tester", "test", "тестер", "тест")
SAMPLE_TERMS = ("sample", "пробник", "vial")
TRAVEL_TERMS = ("travel",)
SET_TERMS = ("set", "набор", "coffret")
NO_BOX_TERMS = ("no box", "without box", "без короб")
GENDER_TERMS = tuple(alias for alias, _display, _group in DEFAULT_AUDIENCE_ALIASES)
NAME_AUDIENCE_TERMS = ("pour femme", "femme", "donna", "pour homme", "homme", "uomo")
REFILL_MODIFIER = "refill"
MINI_MODIFIER = "mini"


@dataclass
class ParseResult:
    raw_name: str
    normalized_text: str
    detected_brand_text: str = ""
    normalized_brand: Brand | None = None
    product_name_text: str = ""
    collection_name: str = ""
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
    for pattern, replacement in get_regex_preprocess_rules():
        text = _safe_regex_sub(pattern, replacement, text)
    text = re.sub(r"\beau de (?:parfum(?:e|ume)?|perfume)\b", "eau de parfum", text)
    text = re.sub(r"\beau de parf\b(?!um)", "eau de parfum", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(\d+)\.0\s*(?=мл|ml)", r"\1 ", text)
    text = re.sub(r"(\d+)\s*мл\.?", r"\1 ml", text)
    text = re.sub(r"\b(edp|edt|edc)(?=\d)", r"\1 ", text)
    text = re.sub(r"(?<=\d)(edp|edt|edc)\b", r" \1", text)
    text = re.sub(r"\b(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)(?=\d)", r"\1 ", text)
    text = re.sub(r"(?<=\d)(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)\b", r" \1", text)
    text = re.sub(r"[\u00a0_\\/,;:|()\[\]{}+]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_concentration_alias_rows():
    rows = cache.get(CONCENTRATION_ALIAS_CACHE_KEY)
    if rows is not None:
        return rows
    rows = [
        (None, normalize_alias_value(alias_text), concentration, False, None, 100)
        for alias_text, concentration in DEFAULT_CONCENTRATION_ALIASES
    ]
    rows.extend(
        ConcentrationAlias.objects.filter(active=True)
        .order_by("supplier__name", "priority", "alias_text")
        .values_list("supplier_id", "normalized_alias", "concentration", "is_regex", "id", "priority")
    )
    rows = sorted(rows, key=lambda row: (row[5], -len(row[1] or ""), row[1] or ""))
    cache.set(CONCENTRATION_ALIAS_CACHE_KEY, rows, 300)
    return rows


def _split_terms(value: str) -> list[str]:
    return [normalize_text(term) for term in re.split(r"[,;\n]+", value or "") if normalize_text(term)]


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(^|\s){re.escape(phrase)}($|\s)", text))


def _contains_any_phrase(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_phrase(text, term) for term in terms)


def _kb_terms(rule_kind: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    terms = [*defaults, *get_parser_terms(rule_kind)]
    seen: set[str] = set()
    normalized_terms: list[str] = []
    for term in terms:
        normalized = normalize_text(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_terms.append(normalized)
    return tuple(sorted(normalized_terms, key=len, reverse=True))


def _tester_terms() -> tuple[str, ...]:
    return _kb_terms("parser_tester_term", TESTER_TERMS)


def _sample_terms() -> tuple[str, ...]:
    return _kb_terms("parser_sample_term", SAMPLE_TERMS)


def _travel_terms() -> tuple[str, ...]:
    return _kb_terms("parser_travel_term", TRAVEL_TERMS)


def _mini_terms() -> tuple[str, ...]:
    return _kb_terms("parser_mini_term", (MINI_MODIFIER, "мини"))


def _set_terms() -> tuple[str, ...]:
    return _kb_terms("parser_set_term", SET_TERMS)


def _refill_terms() -> tuple[str, ...]:
    return _kb_terms("parser_refill_term", ())


def _audience_aliases() -> tuple[tuple[str, str, str], ...]:
    aliases = [
        (normalize_text(alias), display, group)
        for alias, display, group in [*DEFAULT_AUDIENCE_ALIASES, *get_audience_alias_rules()]
    ]
    seen: set[str] = set()
    unique: list[tuple[str, str, str]] = []
    for alias, display, group in sorted(aliases, key=lambda row: len(row[0]), reverse=True):
        if not alias or alias in seen:
            continue
        seen.add(alias)
        unique.append((alias, display, group))
    return tuple(unique)


def audience_group(value: str) -> str:
    normalized = normalize_text(value)
    for alias, display, group in _audience_aliases():
        if normalized in {alias, normalize_text(display)}:
            return group
    return normalized


def _disable_regex_alias(alias, *, pattern: str, exc) -> None:
    logger.warning(
        "regex alias disabled after timeout/error: model=%s id=%s pattern=%s error=%s",
        alias.__class__.__name__,
        alias.pk,
        pattern,
        exc,
    )
    alias.active = False
    alias.save(update_fields=["active", "updated_at"])
    mail_admins(
        "PerfumeX regex alias disabled",
        (
            f"{alias.__class__.__name__} #{alias.pk} was disabled because pattern "
            f"{pattern!r} timed out or failed during matching: {exc}"
        ),
        fail_silently=True,
    )


def _safe_regex_search(pattern: str, text: str, alias=None):
    try:
        return regex.search(pattern, text, timeout=REGEX_ALIAS_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        if alias is not None:
            _disable_regex_alias(alias, pattern=pattern, exc=exc)
        else:
            logger.warning("regex alias skipped after timeout: pattern=%s", pattern)
        return None
    except regex.error as exc:
        if alias is not None:
            _disable_regex_alias(alias, pattern=pattern, exc=exc)
        else:
            logger.warning("regex alias skipped after compile error: pattern=%s error=%s", pattern, exc)
        return None


def _safe_regex_sub(pattern: str, replacement: str, text: str, alias=None) -> str:
    try:
        return regex.sub(pattern, replacement, text, timeout=REGEX_ALIAS_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        if alias is not None:
            _disable_regex_alias(alias, pattern=pattern, exc=exc)
        else:
            logger.warning("regex alias substitution skipped after timeout: pattern=%s", pattern)
        return text
    except regex.error as exc:
        if alias is not None:
            _disable_regex_alias(alias, pattern=pattern, exc=exc)
        else:
            logger.warning(
                "regex alias substitution skipped after compile error: pattern=%s error=%s",
                pattern,
                exc,
            )
        return text


def _strip_known_terms(text: str, terms: list[str]) -> str:
    remaining = text
    for term in [normalize_text(term) for term in terms if term]:
        remaining = re.sub(rf"(^|\s){re.escape(term)}($|\s)", " ", remaining)
    return re.sub(r"\s+", " ", remaining).strip()


def _strip_first_phrase(text: str, phrase: str) -> str:
    normalized_phrase = normalize_text(phrase)
    if not normalized_phrase:
        return text
    return re.sub(rf"(^|\s){re.escape(normalized_phrase)}($|\s)", " ", text, count=1).strip()


def _strip_concentration_aliases(text: str, rows: list[tuple]) -> str:
    remaining = text
    for row in rows:
        _, needle, _value, is_regex, *rest = row
        alias_id = rest[0] if rest else None
        if not needle:
            continue
        if is_regex:
            alias = ConcentrationAlias.objects.filter(pk=alias_id).first() if alias_id else None
            remaining = _safe_regex_sub(needle, " ", remaining, alias=alias)
        else:
            remaining = re.sub(rf"(^|\s){re.escape(needle)}($|\s)", " ", remaining)
    return re.sub(r"\s+", " ", remaining).strip()


def _remaining_after_alias_prefix(text: str, alias_text: str) -> str:
    normalized_alias = normalize_text(alias_text)
    if not normalized_alias:
        return ""
    match = re.search(rf"^\s*{re.escape(normalized_alias)}(?:\s+|$)(?P<remaining>.*)$", text)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group("remaining")).strip()


def _name_bearing_modifiers(product_alias: ProductAlias) -> set[str]:
    alias_identity = normalize_text(" ".join([product_alias.alias_text, product_alias.canonical_text]))
    return {modifier for modifier in MODIFIER_TERMS if _contains_phrase(alias_identity, normalize_text(modifier))}


def _audience_terms_to_strip(audience_aliases: tuple[tuple[str, str, str], ...]) -> list[str]:
    preserved = {normalize_text(term) for term in NAME_AUDIENCE_TERMS}
    terms = [*GENDER_TERMS, *(alias for alias, _display, _group in audience_aliases)]
    return [term for term in terms if normalize_text(term) not in preserved]


def _extract_size(text: str) -> tuple[Decimal | None, str, str]:
    multi_pack_match = re.search(
        r"\b(?P<count>\d{1,2})\s*(?:x|х|×|\*)\s*(?P<size>\d+(?:[.,]\d+)?)\s*(?:ml|мл|м\.л\.?)?\b",
        text,
    )
    if multi_pack_match:
        raw = multi_pack_match.group(0)
        value = Decimal(multi_pack_match.group("size").replace(",", ".")).quantize(Decimal("0.01"))
        count = int(multi_pack_match.group("count"))
        if count < 2 or count > 20:
            return None, "", text
        normalized_raw = f"{count}*{compact_decimal_text(value)}ml"
        return value, normalized_raw, text.replace(raw, " ")

    ml_match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:ml|мл|м\.л\.?)(?=\s|$)", text)
    if ml_match:
        raw = ml_match.group(0)
        value = Decimal(ml_match.group(1).replace(",", ".")).quantize(Decimal("0.01"))
        return value, raw, text.replace(raw, " ")
    reversed_ml_match = re.search(r"\b(?:ml|мл|м\.л\.?)\s*(\d+(?:[.,]\d+)?)(?=\s|$)", text)
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
        "тестер",
        "тест",
        "пробник",
        "мини",
        "набор",
    )
    trailing_terms = tuple({*trailing_terms, *_tester_terms(), *_sample_terms(), *_travel_terms(), *_mini_terms(), *_set_terms(), *_refill_terms()})
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
        pattern = alias.normalized_alias or normalize_alias_value(alias.alias_text)
        if alias.is_regex:
            if _safe_regex_search(pattern, text, alias=alias):
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
    applicable_concentration_aliases = supplier_aliases + global_aliases
    for row in applicable_concentration_aliases:
        _, needle, value, is_regex, *rest = row
        alias_id = rest[0] if rest else None
        if is_regex:
            alias = ConcentrationAlias.objects.filter(pk=alias_id).first() if alias_id else None
            matched = _safe_regex_search(needle, text, alias=alias)
            if not matched:
                continue
            result.concentration = value
            text = _safe_regex_sub(needle, " ", text, alias=alias).strip()
            break
        if re.search(rf"(^|\s){re.escape(needle)}($|\s)", text):
            result.concentration = value
            text = re.sub(rf"(^|\s){re.escape(needle)}($|\s)", " ", text).strip()
            break
    if result.concentration:
        text = _strip_concentration_aliases(text, applicable_concentration_aliases)

    audience_aliases = _audience_aliases()
    for alias_text, display_value, _group in audience_aliases:
        if _contains_phrase(text, alias_text):
            result.supplier_gender_hint = display_value
            break

    tester_terms = _tester_terms()
    sample_terms = _sample_terms()
    travel_terms = _travel_terms()
    mini_terms = _mini_terms()
    set_terms = _set_terms()
    refill_terms = _refill_terms()

    result.is_tester = _contains_any_phrase(text, tester_terms)
    result.is_sample = _contains_any_phrase(text, sample_terms)
    result.is_travel = _contains_any_phrase(text, travel_terms)
    is_mini = _contains_any_phrase(text, mini_terms)
    result.is_set = _contains_any_phrase(text, set_terms)
    if result.raw_size_text and "*" in result.raw_size_text:
        result.is_set = True
    result.packaging = "no_box" if _contains_any_phrase(text, NO_BOX_TERMS) else ""
    result.variant_type = "sample" if result.is_sample else ("travel" if result.is_travel else ("mini" if is_mini else ("set" if result.is_set else ("tester" if result.is_tester else "standard"))))
    result.modifiers = [term for term in MODIFIER_TERMS if re.search(rf"(^|\s){re.escape(term)}($|\s)", text)]
    if is_mini and MINI_MODIFIER not in result.modifiers:
        result.modifiers.append(MINI_MODIFIER)
    if _contains_any_phrase(text, refill_terms):
        result.modifiers.append(REFILL_MODIFIER)

    alias, brand = _match_aliases(text, product.supplier_id)
    if brand:
        result.normalized_brand = brand
        result.detected_brand_text = alias.alias_text if alias else brand.name
        text = _strip_first_phrase(text, result.detected_brand_text)
        if alias and alias.supplier_id:
            result.warnings.append("supplier-specific alias overrode global alias")

    if not result.size_ml:
        size, raw_size, compact_text = _extract_loose_trailing_size(text)
        if size is not None:
            result.size_ml = size
            result.raw_size_text = raw_size
            text = compact_text

    product_alias_match_text = _strip_known_terms(
        text,
        [
            *_audience_terms_to_strip(audience_aliases),
            *tester_terms,
            *sample_terms,
            *travel_terms,
            *mini_terms,
            *set_terms,
            *refill_terms,
            *NO_BOX_TERMS,
        ],
    )
    product_aliases = ProductAlias.objects.filter(active=True).order_by("supplier_id", "priority", "-alias_text")
    if result.normalized_brand:
        product_aliases = product_aliases.filter(Q(brand_id=result.normalized_brand.id) | Q(brand__isnull=True))
    for product_alias in list(product_aliases.filter(supplier_id=product.supplier_id)) + list(product_aliases.filter(supplier__isnull=True)):
        alias_text = normalize_text(product_alias.alias_text)
        excluded_terms = _split_terms(product_alias.excluded_terms)
        if alias_text and _contains_phrase(product_alias_match_text, alias_text) and not any(_contains_phrase(text, term) for term in excluded_terms):
            if product_alias.collection_name:
                result.collection_name = product_alias.collection_name
            if not product_alias.canonical_text:
                text = _strip_known_terms(text, [alias_text])
                product_alias_match_text = _strip_known_terms(product_alias_match_text, [alias_text])
                continue
            result.product_name_text = product_alias.canonical_text
            remaining_name = _remaining_after_alias_prefix(product_alias_match_text, alias_text)
            if remaining_name and result.concentration and result.size_ml:
                result.product_name_text = f"{result.product_name_text} {remaining_name}".strip()[:255]
            name_modifiers = _name_bearing_modifiers(product_alias)
            if name_modifiers:
                result.modifiers = [modifier for modifier in result.modifiers if modifier not in name_modifiers]
            if product_alias.concentration and result.concentration and product_alias.supplier_id == product.supplier_id:
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
        result.collection_name = perfume.collection_name
        if perfume.audience:
            result.supplier_gender_hint = perfume.audience
        if product.catalog_variant_id:
            variant = product.catalog_variant
            if variant.size_ml and not result.size_ml:
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
                *_audience_terms_to_strip(audience_aliases),
                *tester_terms,
                *sample_terms,
                *travel_terms,
                *mini_terms,
                *set_terms,
                *refill_terms,
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
            "collection_name": parsed.collection_name,
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
