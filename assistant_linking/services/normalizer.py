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
    BAG_MODIFIER,
    CONCENTRATION_ALIAS_CACHE_KEY,
    COSMETIC_PUDRE_MODIFIER,
    DECANT_MODIFIER,
    DEODORANT_MODIFIER,
    HAIR_CARE_CATEGORY_CONCENTRATIONS,
    MANUAL_REVIEW_MODIFIER,
    PERFUME_CATEGORY_CONCENTRATIONS,
    VINTAGE_MODIFIER,
    BrandAlias,
    ConcentrationAlias,
    ParsedSupplierProduct,
    ProductAlias,
    display_label,
    display_title,
)
from assistant_linking.services.garbage import (
    GARBAGE_MODIFIER,
    GARBAGE_WARNING_PREFIX,
    match_garbage_keyword,
    match_trailing_garbage_marker,
)
from assistant_linking.services.parser_rules import (
    get_audience_alias_rules,
    get_parser_terms,
    get_regex_preprocess_rules,
    get_variant_type_alias_rules,
)
from assistant_linking.utils.text import (
    normalize_alias_value,
    normalize_mixed_script_latin_lookalikes,
)
from catalog.models import Brand, Perfume, compact_decimal_text
from prices.models import SupplierProduct


logger = logging.getLogger(__name__)
PARSER_VERSION = "deterministic-v34"
REGEX_ALIAS_TIMEOUT_SECONDS = 1.0
CATALOG_CONCENTRATION_CONFLICT_WARNING = (
    "Catalogue match suggests {suggested}. Supplier text parsed as {parsed}."
)
GENERATED_BRAND_ALIAS_STOPWORDS = {
    "atelier",
    "beauty",
    "collection",
    "company",
    "cosmetics",
    "edition",
    "fragrance",
    "fragrances",
    "inc",
    "limited",
    "ltd",
    "parfum",
    "parfums",
    "paris",
    "perfume",
    "perfumes",
}

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
    ("u", "Unisex", "unisex"),
    ("унисекс", "Unisex", "unisex"),
    ("уни", "Unisex", "unisex"),
)

MODIFIER_TERMS = (
    "intense",
    "elixir",
    "absolu",
    "eau intense",
    "extreme",
    "sport",
    "fraiche",
    "fraicheur",
)
TESTER_TERMS = ("tester", "test", "tectep", "тестер", "тест")
SAMPLE_TERMS = ("sample", "пробник", "vial")
TRAVEL_TERMS = ("travel",)
SET_TERMS = ("set", "набор", "coffret")
BAG_TERMS = ("пакет",)
COSMETIC_PUDRE_TERMS = ("пудра",)
DEODORANT_TERMS = (
    "deo",
    "deodorant",
    "deo spray",
    "deodorant spray",
    "deo stick",
    "deostick",
    "дезодорант",
)
DECANT_TERMS = (
    "\u043e\u0442\u043b\u0438\u0432 \u0438\u0437 \u0444\u043b\u0430\u043a\u043e\u043d\u0430",
    "\u043e\u0442\u043b\u0438\u0432\u0430\u043d\u0442",
    "\u043e\u0442\u043b\u0438\u0432\u0430",
    "\u043e\u0442\u043b\u0438\u0432\u0430\u043d",
    "\u043e\u0442\u043b\u0438\u0432",
    "\u043e\u0442\u043b",
)
VINTAGE_TERMS = (
    "\u0432\u0438\u043d\u0442\u0430\u0436",
    "\u0432\u0438\u043d\u0442",
    "vintage",
    "vint",
)
NO_BOX_TERMS = (
    "no box",
    "without box",
    "без короб",
    "без коробки",
    "б к",
    "бк",
    "b k",
    "bk",
)
WOODBOX_TERMS = ("woodbox", "wood box")
NEW_DESIGN_PACKAGING_TERMS = (
    "new design",
    "new box",
    "new packaging",
    "новый дизайн",
    "новая упаковка",
    "нов дизайн",
    "нов. дизайн",
    "нов диз",
    "нов. диз",
    "нов ди",
    "нов. ди",
)
OLD_DESIGN_PACKAGING_TERMS = (
    "old design",
    "old box",
    "old packaging",
    "старый дизайн",
    "ст дизайн",
    "ст. дизайн",
    "ст.дизайн",
    "ст диз",
    "ст. диз",
    "ст.диз",
    "ст д",
    "ст.д",
)
WITH_CAP_PACKAGING_TERMS = (
    "with cap",
    "with lid",
    "с крышкой",
    "с крыш",
    "с фирм крышкой",
    "с фирм. крышкой",
    "с фирм.крышкой",
    "с фирм.крыш.",
    "с фирм кр",
    "с фирм. кр",
    "с фирм.кр",
    "с фир.крыш.",
    "с фирменной крышкой",
    "c фирм крышкой",
    "c фирм. крышкой",
    "c фирм.крышкой",
    "c фирм.крыш.",
    "c фирм кр",
    "c фирм. кр",
    "c фирм.кр",
    "c фир.крыш.",
    "фирм крыш",
    "фирм. крыш",
    "фирм.крыш",
    "фирм.крыш.",
    "фир крыш",
    "фир.крыш",
    "фир.крыш.",
)
DENTED_PACKAGING_TERMS = (
    "dented",
    "creased",
    "подмятая",
    "подмятый",
    "подмятые",
    "подмято",
    "подмята",
    "подмят",
    "помятая",
    "помятый",
    "помятые",
    "помято",
    "помята",
    "помят",
)
REFILLABLE_PACKAGING_TERMS = ("refillable",)
SUPPLIER_COMMENT_TERMS = (
    "\u0431\u0435\u043b\u044b\u0439",
    "\u0431\u0435\u043b\u0430\u044f",
    "\u0431\u0435\u043b\u043e\u0435",
    "\u0431\u0435\u043b",
)
DECODED_TERMS = ("decoded", "dec", "декод", "декодированный")
GRAY_BOX_TERMS = ("gray box", "grey box", "серый бокс", "серый короб", "серая коробка")
GRAY_BOX_COLOR_TERMS = ("серый", "серая", "сер", "gray", "grey")
GENDER_TERMS = tuple(alias for alias, _display, _group in DEFAULT_AUDIENCE_ALIASES)
NAME_AUDIENCE_TERMS = (
    "pour femme",
    "femme",
    "donna",
    "for woman",
    "for women",
    "for her",
    "her",
    "pour homme",
    "homme",
    "uomo",
    "for man",
    "for men",
    "for him",
    "man",
)
NAME_BEARING_MODIFIER_PHRASES = ("eau fraiche", "eau fraicheur")
AUDIENCE_NAME_SUFFIXES = {
    "women": (
        "woman",
        "women",
        "lady",
        "for woman",
        "for women",
        "for her",
        "her",
        "pour femme",
        "femme",
    ),
    "men": ("man", "men", "for man", "for men", "for him", "pour homme", "homme"),
}
AUDIENCE_DISAMBIGUATION_SUFFIXES = {
    "women": "Woman",
    "men": "Man",
}
TRAILING_MARKETING_GARBAGE_TERMS = ("new", "exclusive", "exlusive")
NAME_EXTENSION_SUFFIXES = ("limited edition",)
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
    release_year: int | None = None
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

    @property
    def display_brand(self) -> str:
        if self.normalized_brand:
            return str(self.normalized_brand)
        return self.detected_brand_text

    @property
    def normalized_brand_id(self) -> int | None:
        return self.normalized_brand.pk if self.normalized_brand else None

    @property
    def display_size(self) -> str:
        if self.raw_size_text and "*" in self.raw_size_text:
            return self.raw_size_text
        if self.size_ml is None:
            return ""
        return f"{compact_decimal_text(self.size_ml)}ml"

    @property
    def display_product_name(self) -> str:
        return display_title(self.product_name_text)

    @property
    def display_collection_name(self) -> str:
        return display_title(self.collection_name)

    @property
    def display_variant_type(self) -> str:
        if BAG_MODIFIER in (self.modifiers or []) or self.variant_type == BAG_MODIFIER:
            return "Bag"
        if (
            COSMETIC_PUDRE_MODIFIER in (self.modifiers or [])
            or self.variant_type == "poudre"
        ):
            return "Poudre"
        if (
            DEODORANT_MODIFIER in (self.modifiers or [])
            or self.variant_type == DEODORANT_MODIFIER
        ):
            return "Deodorant"
        if (
            DECANT_MODIFIER in (self.modifiers or [])
            or self.variant_type == DECANT_MODIFIER
        ):
            return "Decant"
        if (
            VINTAGE_MODIFIER in (self.modifiers or [])
            or self.variant_type == VINTAGE_MODIFIER
        ):
            return "Vintage"
        if self.variant_type == "decoded":
            return "Decoded"
        if self.is_tester or self.variant_type == "tester":
            return "Tester"
        if self.is_sample or self.variant_type == "sample":
            return "Sample"
        if self.is_travel or self.variant_type == "travel":
            return "Travel"
        if self.is_set or self.variant_type == "set":
            return "Set"
        if REFILL_MODIFIER in (self.modifiers or []):
            return "Refill"
        return display_label(self.variant_type, default="Standard")

    @property
    def product_category_label(self) -> str:
        if BAG_MODIFIER in (self.modifiers or []) or self.variant_type == BAG_MODIFIER:
            return "Bags"
        if (
            COSMETIC_PUDRE_MODIFIER in (self.modifiers or [])
            or self.variant_type == "poudre"
        ):
            return "Cosmetics"
        if (
            DEODORANT_MODIFIER in (self.modifiers or [])
            or self.variant_type == DEODORANT_MODIFIER
        ):
            return "Deodorants"
        if (
            DECANT_MODIFIER in (self.modifiers or [])
            or self.variant_type == DECANT_MODIFIER
        ):
            return "Decants"
        if (
            VINTAGE_MODIFIER in (self.modifiers or [])
            or self.variant_type == VINTAGE_MODIFIER
        ):
            return "Vintage"
        if self.concentration in HAIR_CARE_CATEGORY_CONCENTRATIONS:
            return "Hair Care"
        if self.concentration in PERFUME_CATEGORY_CONCENTRATIONS:
            return "Perfume"
        return "Unknown"

    @property
    def product_subcategory_label(self) -> str:
        if (
            COSMETIC_PUDRE_MODIFIER in (self.modifiers or [])
            or self.variant_type == "poudre"
        ):
            return "Poudre"
        return ""

    @property
    def display_packaging(self) -> str:
        return display_label(self.packaging, default="Standard")

    @property
    def identity_variant_label(self) -> str:
        if self.product_category_label in {"Bags", "Cosmetics", "Deodorants"}:
            return ""
        variant = self.display_variant_type
        if variant and variant != "Standard":
            return variant
        return ""

    @property
    def identity_packaging_label(self) -> str:
        packaging = self.display_packaging
        if (
            packaging
            and packaging != "Standard"
            and packaging != self.identity_variant_label
        ):
            return packaging
        return ""

    @property
    def display_identity(self) -> str:
        product_name = self.display_product_name
        if product_name and normalize_alias_value(
            product_name
        ) == normalize_alias_value(self.display_brand):
            product_name = ""
        parts = [
            self.display_brand,
            self.display_collection_name,
            product_name,
            self.concentration,
            self.display_size,
            self.identity_variant_label,
            self.identity_packaging_label,
        ]
        return " / ".join(part for part in parts if part)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = normalize_mixed_script_latin_lookalikes(text).lower()
    for pattern, replacement in get_regex_preprocess_rules():
        text = _safe_regex_sub(pattern, replacement, text)
    text = re.sub(r"(?<=[a-z])(?=[а-яё])|(?<=[а-яё])(?=[a-z])", " ", text)
    text = re.sub(r"\b[tт][eе][sсc][tт][eе][rрp](?:[сc])?\b", " tester ", text)
    text = re.sub(r"\beau de (?:parfum(?:e|ume)?|perfume)\b", "eau de parfum", text)
    text = re.sub(r"\beau de parf\b(?!um)", "eau de parfum", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(\d+)\.0\s*(?=мл|ml)", r"\1 ", text)
    text = re.sub(r"(\d+)\s*мл\.?", r"\1 ml", text)
    text = re.sub(r"\b(edp|edt|edc)(?=[a-zа-яё]|\d)", r"\1 ", text)
    text = re.sub(r"(?<=\d)(edp|edt|edc)\b", r" \1", text)
    text = re.sub(
        r"\b(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)(?=\d)",
        r"\1 ",
        text,
    )
    text = re.sub(
        r"(?<=\d)(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)\b",
        r" \1",
        text,
    )
    text = re.sub(r"(?<=[a-zа-яё])(?=\d+(?:\s)?(?:ml|мл)\b)", " ", text)
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
        .values_list(
            "supplier_id",
            "normalized_alias",
            "concentration",
            "is_regex",
            "id",
            "priority",
        )
    )
    rows = sorted(rows, key=lambda row: (row[5], -len(row[1] or ""), row[1] or ""))
    cache.set(CONCENTRATION_ALIAS_CACHE_KEY, rows, 300)
    return rows


def _split_terms(value: str) -> list[str]:
    return [
        normalize_text(term)
        for term in re.split(r"[,;\n]+", value or "")
        if normalize_text(term)
    ]


def _phrase_pattern(phrase: str) -> str:
    escaped = re.escape(normalize_text(phrase))
    escaped = escaped.replace(r"\.", r"(?:\.\s*|\s+)")
    escaped = escaped.replace(r"\ \&\ ", r"(?:\s*&\s*|\s+)")
    escaped = escaped.replace(r"\&", r"(?:&|\s+)")
    escaped = escaped.replace(r"\ ", r"(?:\s+|\.\s*)")
    return rf"(?<![a-z0-9а-яё]){escaped}(?![a-z0-9а-яё])"


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = normalize_text(phrase)
    if not normalized_phrase:
        return False
    return bool(re.search(_phrase_pattern(normalized_phrase), text))


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


def _bag_terms() -> tuple[str, ...]:
    return _kb_terms("parser_bag_term", BAG_TERMS)


def _cosmetic_poudre_terms() -> tuple[str, ...]:
    return _kb_terms("parser_cosmetic_poudre_term", COSMETIC_PUDRE_TERMS)


def _deodorant_terms() -> tuple[str, ...]:
    return _kb_terms("parser_deodorant_term", DEODORANT_TERMS)


def _decant_terms() -> tuple[str, ...]:
    return _kb_terms("parser_decant_term", DECANT_TERMS)


def _vintage_terms() -> tuple[str, ...]:
    return _kb_terms("parser_vintage_term", VINTAGE_TERMS)


def _refill_terms() -> tuple[str, ...]:
    return _kb_terms("parser_refill_term", ())


def _dented_packaging_terms() -> tuple[str, ...]:
    return _kb_terms("parser_dented_packaging_term", DENTED_PACKAGING_TERMS)


def _refillable_packaging_terms() -> tuple[str, ...]:
    return _kb_terms("parser_refillable_packaging_term", REFILLABLE_PACKAGING_TERMS)


def _supplier_comment_terms() -> tuple[str, ...]:
    return _kb_terms("parser_supplier_comment_term", SUPPLIER_COMMENT_TERMS)


def _with_cap_packaging_terms() -> tuple[str, ...]:
    return _kb_terms("parser_with_cap_packaging_term", WITH_CAP_PACKAGING_TERMS)


def _old_design_packaging_terms() -> tuple[str, ...]:
    return _kb_terms("parser_old_design_packaging_term", OLD_DESIGN_PACKAGING_TERMS)


def _decoded_terms() -> tuple[str, ...]:
    return _kb_terms("parser_decoded_term", DECODED_TERMS)


def _variant_type_aliases() -> tuple[tuple[str, str], ...]:
    aliases = [
        (normalize_text(alias), normalize_text(variant_type).replace(" ", "_"))
        for alias, variant_type in get_variant_type_alias_rules()
    ]
    return tuple(
        (alias, variant_type)
        for alias, variant_type in aliases
        if alias and variant_type
    )


def _variant_type_terms() -> tuple[str, ...]:
    return tuple(alias for alias, _variant_type in _variant_type_aliases())


def _structured_packaging_terms() -> tuple[str, ...]:
    return (
        *NO_BOX_TERMS,
        *WOODBOX_TERMS,
        *NEW_DESIGN_PACKAGING_TERMS,
        *_old_design_packaging_terms(),
        *_with_cap_packaging_terms(),
        *_dented_packaging_terms(),
        *_refillable_packaging_terms(),
        *GRAY_BOX_TERMS,
        *GRAY_BOX_COLOR_TERMS,
    )


def _audience_aliases() -> tuple[tuple[str, str, str], ...]:
    aliases = [
        (normalize_text(alias), display, group)
        for alias, display, group in [
            *DEFAULT_AUDIENCE_ALIASES,
            *get_audience_alias_rules(),
        ]
    ]
    seen: set[str] = set()
    unique: list[tuple[str, str, str]] = []
    for alias, display, group in sorted(
        aliases, key=lambda row: len(row[0]), reverse=True
    ):
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
    if not alias.active:
        return
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
            logger.warning(
                "regex alias skipped after compile error: pattern=%s error=%s",
                pattern,
                exc,
            )
        return None


def _safe_regex_sub(pattern: str, replacement: str, text: str, alias=None) -> str:
    try:
        return regex.sub(
            pattern, replacement, text, timeout=REGEX_ALIAS_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        if alias is not None:
            _disable_regex_alias(alias, pattern=pattern, exc=exc)
        else:
            logger.warning(
                "regex alias substitution skipped after timeout: pattern=%s", pattern
            )
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


def _phrase_spans(
    text: str, phrases: tuple[str, ...] | list[str]
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for phrase in phrases:
        normalized_phrase = normalize_text(phrase)
        if not normalized_phrase:
            continue
        spans.extend(
            match.span()
            for match in re.finditer(_phrase_pattern(normalized_phrase), text)
        )
    return spans


def _strip_known_terms(
    text: str,
    terms: list[str],
    *,
    preserve_phrases: tuple[str, ...] | list[str] = (),
) -> str:
    remaining = text
    for term in [normalize_text(term) for term in terms if term]:
        preserved_spans = _phrase_spans(remaining, preserve_phrases)

        def replace(match):
            if any(
                match.start() >= span_start and match.end() <= span_end
                for span_start, span_end in preserved_spans
            ):
                return match.group(0)
            return " "

        remaining = re.sub(_phrase_pattern(term), replace, remaining)
    return re.sub(r"\s+", " ", remaining).strip()


def _strip_first_phrase(text: str, phrase: str) -> str:
    normalized_phrase = normalize_text(phrase)
    if not normalized_phrase:
        return text
    return re.sub(_phrase_pattern(normalized_phrase), " ", text, count=1).strip()


def _strip_concentration_aliases(text: str, rows: list[tuple]) -> str:
    remaining = text
    for row in rows:
        _, needle, _value, is_regex, *rest = row
        alias_id = rest[0] if rest else None
        if not needle:
            continue
        if is_regex:
            alias = (
                ConcentrationAlias.objects.filter(pk=alias_id).first()
                if alias_id
                else None
            )
            remaining = _safe_regex_sub(needle, " ", remaining, alias=alias)
        else:
            remaining = re.sub(rf"(^|\s){re.escape(needle)}($|\s)", " ", remaining)
    return re.sub(r"\s+", " ", remaining).strip()


def _remaining_after_alias_prefix(text: str, alias_text: str) -> str:
    normalized_alias = normalize_text(alias_text)
    if not normalized_alias:
        return ""
    match = re.search(
        rf"^\s*{re.escape(normalized_alias)}(?:\s+|$)(?P<remaining>.*)$", text
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group("remaining")).strip()


def _product_name_from_alias_match_context(
    text: str, alias_text: str, canonical_text: str
) -> str:
    normalized_alias = normalize_text(alias_text)
    name_bearing_audience_terms = {
        normalize_text(term)
        for term in [
            *NAME_AUDIENCE_TERMS,
            *(
                suffix
                for suffixes in AUDIENCE_NAME_SUFFIXES.values()
                for suffix in suffixes
            ),
        ]
    }
    if normalize_text(canonical_text) not in name_bearing_audience_terms:
        return canonical_text
    match = re.search(
        rf"^(?P<prefix>.*?)\b{re.escape(normalized_alias)}\b(?P<suffix>.*)$", text
    )
    if not match:
        return canonical_text
    prefix = re.sub(r"\s+", " ", match.group("prefix")).strip()
    suffix = re.sub(r"\s+", " ", match.group("suffix")).strip()
    if re.fullmatch(r"(?:19|20)\d{2}", suffix):
        suffix = ""
    parts = [prefix, canonical_text, suffix]
    return (
        re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()
        or canonical_text
    )


def _clean_product_name_text(value: str) -> str:
    name = re.sub(r"\s+", " ", value or "").strip()
    name = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", name)
    name = re.sub(r"\s+(?:19|20)\d{2}$", "", name).strip()
    name = re.sub(r"[\s,.;:_-]+$", "", name).strip()
    return name[:255]


def _strip_trailing_supplier_status_garbage(result: ParseResult) -> str:
    if not result.product_name_text or not result.size_ml:
        return result.product_name_text
    name = result.product_name_text.strip()
    stripped = re.sub(r"\s+new[\W_]*$", "", name, flags=re.IGNORECASE).strip()
    return _clean_product_name_text(stripped) or result.product_name_text


def _strip_redundant_trailing_audience_suffix(result: ParseResult) -> str:
    if not result.product_name_text or not result.supplier_gender_hint:
        return result.product_name_text
    suffixes = AUDIENCE_NAME_SUFFIXES.get(
        audience_group(result.supplier_gender_hint), ()
    )
    if not suffixes:
        return result.product_name_text
    name = result.product_name_text
    for suffix in sorted(suffixes, key=len, reverse=True):
        suffix_pattern = re.escape(normalize_text(suffix)).replace(r"\ ", r"\s+")
        match = re.match(
            rf"^(?P<base>.+)\s+{suffix_pattern}$", name, flags=re.IGNORECASE
        )
        if not match:
            continue
        base = match.group("base").strip()
        if any(
            normalize_text(other) != normalize_text(suffix)
            and _contains_phrase(base, other)
            for other in suffixes
        ):
            return _clean_product_name_text(base)
    return result.product_name_text


def _strip_cyrillic_name_tokens_for_latin_brand(result: ParseResult) -> str:
    if not result.normalized_brand or not result.product_name_text:
        return result.product_name_text
    brand_name = result.normalized_brand.name
    if re.search(r"[а-яё]", brand_name, flags=re.IGNORECASE) or not re.search(
        r"[a-z]", brand_name, flags=re.IGNORECASE
    ):
        return result.product_name_text
    if not re.search(r"[a-z]", result.product_name_text, flags=re.IGNORECASE):
        return result.product_name_text
    if not re.search(r"[а-яё]", result.product_name_text, flags=re.IGNORECASE):
        return result.product_name_text
    stripped = re.sub(
        r"(?<!\S)\S*[а-яё]\S*(?!\S)", " ", result.product_name_text, flags=re.IGNORECASE
    )
    stripped = _clean_product_name_text(stripped)
    return stripped or result.product_name_text


def _catalog_scent_key(value: str) -> str:
    return re.sub(r"[^a-z0-9а-я]+", "", normalize_text(value))


def _catalog_perfume_matches_parse_context(
    perfume: Perfume, result: ParseResult
) -> bool:
    if result.concentration and perfume.concentration:
        if normalize_text(result.concentration) != normalize_text(
            perfume.concentration
        ):
            return False
    if result.collection_name and perfume.collection_name:
        if _catalog_scent_key(result.collection_name) != _catalog_scent_key(
            perfume.collection_name
        ):
            return False
    if result.supplier_gender_hint and perfume.audience:
        if audience_group(result.supplier_gender_hint) != audience_group(
            perfume.audience
        ):
            return False
    return True


def _catalog_perfume_matches_non_audience_context(
    perfume: Perfume, result: ParseResult
) -> bool:
    if result.concentration and perfume.concentration:
        if normalize_text(result.concentration) != normalize_text(
            perfume.concentration
        ):
            return False
    if result.collection_name and perfume.collection_name:
        if _catalog_scent_key(result.collection_name) != _catalog_scent_key(
            perfume.collection_name
        ):
            return False
    return True


def _catalog_base_keys_without_trailing_audience(result: ParseResult) -> set[str]:
    audience_suffixes = AUDIENCE_NAME_SUFFIXES.get(
        audience_group(result.supplier_gender_hint), ()
    )
    if not audience_suffixes:
        return set()
    normalized_name = normalize_text(result.product_name_text)
    base_keys: set[str] = set()
    for suffix in audience_suffixes:
        normalized_suffix = normalize_text(suffix)
        match = re.match(
            rf"^(?P<base>.+)\s+{re.escape(normalized_suffix)}$", normalized_name
        )
        if not match:
            continue
        base_key = _catalog_scent_key(match.group("base"))
        if len(base_key) >= 3:
            base_keys.add(base_key)
    return base_keys


def _catalog_keys_with_equivalent_trailing_audience(result: ParseResult) -> set[str]:
    audience_suffixes = AUDIENCE_NAME_SUFFIXES.get(
        audience_group(result.supplier_gender_hint), ()
    )
    if not audience_suffixes:
        return set()
    normalized_name = normalize_text(result.product_name_text)
    keys: set[str] = set()
    for suffix in audience_suffixes:
        normalized_suffix = normalize_text(suffix)
        match = re.match(
            rf"^(?P<base>.+)\s+{re.escape(normalized_suffix)}$", normalized_name
        )
        if not match:
            continue
        base_key = _catalog_scent_key(match.group("base"))
        if len(base_key) < 2:
            continue
        keys.update(
            f"{base_key}{_catalog_scent_key(other)}"
            for other in audience_suffixes
            if other
        )
    return {key for key in keys if len(key) >= 3}


def _catalog_has_audience_named_sibling(
    perfumes: list[Perfume], base_key: str, result: ParseResult
) -> bool:
    suffix_keys = {
        _catalog_scent_key(suffix)
        for suffixes in AUDIENCE_NAME_SUFFIXES.values()
        for suffix in suffixes
    }
    audience_variant_keys = {
        f"{base_key}{suffix_key}" for suffix_key in suffix_keys if suffix_key
    }
    return any(
        _catalog_perfume_matches_non_audience_context(perfume, result)
        and _catalog_scent_key(perfume.name) in audience_variant_keys
        for perfume in perfumes
    )


def _catalog_base_keys_without_trailing_marketing_garbage(
    result: ParseResult,
) -> set[str]:
    compact_name = _catalog_scent_key(result.product_name_text)
    base_keys: set[str] = set()
    changed = True
    while changed:
        changed = False
        for term in TRAILING_MARKETING_GARBAGE_TERMS:
            term_key = _catalog_scent_key(term)
            if not term_key or not compact_name.endswith(term_key):
                continue
            compact_name = compact_name[: -len(term_key)]
            if len(compact_name) >= 3:
                base_keys.add(compact_name)
            changed = True
            break
    return base_keys


def _catalog_name_without_trailing_audience_key(value: str) -> str:
    normalized_name = normalize_text(value)
    suffixes = [
        suffix
        for suffix_group in AUDIENCE_NAME_SUFFIXES.values()
        for suffix in suffix_group
    ]
    for suffix in sorted(suffixes, key=len, reverse=True):
        normalized_suffix = normalize_text(suffix)
        match = re.match(
            rf"^(?P<base>.+)\s+{re.escape(normalized_suffix)}$", normalized_name
        )
        if match:
            return _catalog_scent_key(match.group("base"))
    return _catalog_scent_key(value)


def _catalog_name_matches_audience_hint(value: str, result: ParseResult) -> bool:
    suffixes = AUDIENCE_NAME_SUFFIXES.get(
        audience_group(result.supplier_gender_hint), ()
    )
    if not suffixes:
        return False
    normalized_name = normalize_text(value)
    return any(
        re.search(rf"\s+{re.escape(normalize_text(suffix))}$", normalized_name)
        for suffix in sorted(suffixes, key=len, reverse=True)
    )


def _catalog_name_with_audience_disambiguation(value: str, result: ParseResult) -> str:
    group = audience_group(result.supplier_gender_hint)
    suffix = AUDIENCE_DISAMBIGUATION_SUFFIXES.get(group)
    if not value or not suffix or _catalog_name_matches_audience_hint(value, result):
        return value
    return f"{display_title(value)} {suffix}"


def _catalog_has_same_name_audience_conflict(
    perfumes: list[Perfume], key: str, result: ParseResult
) -> bool:
    group = audience_group(result.supplier_gender_hint)
    if group not in AUDIENCE_DISAMBIGUATION_SUFFIXES:
        return False
    matching_groups = {
        audience_group(perfume.audience)
        for perfume in perfumes
        if _catalog_perfume_matches_non_audience_context(perfume, result)
        and _catalog_scent_key(perfume.name) == key
    }
    matching_groups.discard("")
    return group in matching_groups and len(matching_groups) > 1


def _catalog_name_from_audience_only_context(
    result: ParseResult,
    candidate_text: str,
    audience_aliases: tuple[tuple[str, str, str], ...],
) -> str:
    if (
        not result.normalized_brand
        or not result.normalized_brand.id
        or not result.supplier_gender_hint
    ):
        return ""
    candidate_key = _catalog_scent_key(candidate_text)
    if len(candidate_key) < 2:
        return ""
    expected_group = audience_group(result.supplier_gender_hint)
    audience_terms = {
        _catalog_scent_key(alias)
        for alias, _display, group in audience_aliases
        if group == expected_group
    }
    audience_terms.update(
        _catalog_scent_key(display)
        for _alias, display, group in audience_aliases
        if group == expected_group
    )
    audience_terms = {term for term in audience_terms if len(term) > 1}
    if candidate_key not in audience_terms:
        token_keys = [
            _catalog_scent_key(token)
            for token in normalize_text(candidate_text).split()
        ]
        non_audience_tokens = [
            token_key for token_key in token_keys if token_key not in audience_terms
        ]
        audience_token_keys = [
            token_key for token_key in token_keys if token_key in audience_terms
        ]
        if non_audience_tokens or not audience_token_keys:
            return ""
        candidate_key = max(audience_token_keys, key=len)
    brand_prefix_keys = {
        _catalog_scent_key(result.normalized_brand.name),
        *(
            _catalog_scent_key(alias)
            for alias in _generated_brand_alias_candidates(result.normalized_brand)
        ),
    }
    brand_prefix_keys = {key for key in brand_prefix_keys if key}
    names = {
        perfume.name
        for perfume in Perfume.objects.filter(brand_id=result.normalized_brand.id).only(
            "name", "concentration", "audience", "collection_name"
        )
        if _catalog_perfume_matches_parse_context(perfume, result)
        and (
            _catalog_scent_key(perfume.name) == candidate_key
            or any(
                _catalog_scent_key(perfume.name) == f"{prefix}{candidate_key}"
                for prefix in brand_prefix_keys
            )
        )
    }
    if len(names) == 1:
        return names.pop()
    return ""


def _catalog_name_with_audience_before_name_extension(
    result: ParseResult, perfumes: list[Perfume]
) -> str:
    if not result.supplier_gender_hint:
        return result.product_name_text
    normalized_name = normalize_text(result.product_name_text)
    for suffix in NAME_EXTENSION_SUFFIXES:
        normalized_suffix = normalize_text(suffix)
        match = re.match(
            rf"^(?P<base>.+)\s+{re.escape(normalized_suffix)}$", normalized_name
        )
        if not match:
            continue
        base_key = _catalog_scent_key(match.group("base"))
        if len(base_key) < 3:
            continue
        candidates = {
            f"{perfume.name} {display_title(suffix)}"
            for perfume in perfumes
            if _catalog_perfume_matches_non_audience_context(perfume, result)
            and _catalog_name_matches_audience_hint(perfume.name, result)
            and _catalog_name_without_trailing_audience_key(perfume.name) == base_key
        }
        if len(candidates) == 1:
            return candidates.pop()
    return result.product_name_text


def _canonicalize_product_name_from_catalog(result: ParseResult) -> str:
    if (
        not result.normalized_brand
        or not result.normalized_brand.id
        or not result.product_name_text
    ):
        return result.product_name_text
    key = _catalog_scent_key(result.product_name_text)
    if len(key) < 3:
        return result.product_name_text
    perfumes = list(
        Perfume.objects.filter(brand_id=result.normalized_brand.id).only(
            "name",
            "concentration",
            "audience",
            "collection_name",
        )
    )
    if _catalog_has_same_name_audience_conflict(perfumes, key, result):
        return _catalog_name_with_audience_disambiguation(
            result.product_name_text,
            result,
        )
    equivalent_audience_keys = _catalog_keys_with_equivalent_trailing_audience(result)
    if equivalent_audience_keys:
        names = {
            perfume.name
            for perfume in perfumes
            if _catalog_perfume_matches_parse_context(perfume, result)
            and _catalog_scent_key(perfume.name) in equivalent_audience_keys
        }
        if len(names) == 1:
            return names.pop()

    audience_suffixes = AUDIENCE_NAME_SUFFIXES.get(
        audience_group(result.supplier_gender_hint), ()
    )
    if audience_suffixes:
        audience_variant_keys = {
            f"{key}{_catalog_scent_key(suffix)}" for suffix in audience_suffixes
        }
        names = {
            perfume.name
            for perfume in perfumes
            if _catalog_perfume_matches_parse_context(perfume, result)
            and _catalog_scent_key(perfume.name) in audience_variant_keys
        }
        if len(names) == 1:
            return names.pop()

    names = {
        perfume.name
        for perfume in perfumes
        if _catalog_perfume_matches_parse_context(perfume, result)
        and _catalog_scent_key(perfume.name) == key
    }
    if len(names) == 1:
        return names.pop()
    names = {
        perfume.name
        for perfume in perfumes
        if _catalog_scent_key(perfume.name) == key
        and (
            not result.collection_name
            or not perfume.collection_name
            or _catalog_scent_key(result.collection_name)
            == _catalog_scent_key(perfume.collection_name)
        )
        and (
            not result.supplier_gender_hint
            or not perfume.audience
            or audience_group(result.supplier_gender_hint)
            == audience_group(perfume.audience)
        )
    }
    if len(names) == 1:
        return names.pop()

    base_keys = _catalog_base_keys_without_trailing_audience(result)
    if base_keys:
        names = {
            perfume.name
            for perfume in perfumes
            if _catalog_perfume_matches_parse_context(perfume, result)
            and _catalog_scent_key(perfume.name) in base_keys
        }
        if len(names) == 1:
            base_key = next(iter(base_keys))
            if _catalog_has_audience_named_sibling(perfumes, base_key, result):
                return result.product_name_text
            return names.pop()

    base_keys = _catalog_base_keys_without_trailing_marketing_garbage(result)
    if base_keys:
        names = {
            perfume.name
            for perfume in perfumes
            if _catalog_perfume_matches_non_audience_context(perfume, result)
            and _catalog_scent_key(perfume.name) in base_keys
        }
        if len(names) == 1:
            return names.pop()

    name_with_audience_extension = _catalog_name_with_audience_before_name_extension(
        result, perfumes
    )
    if name_with_audience_extension != result.product_name_text:
        return name_with_audience_extension
    return result.product_name_text


def _product_name_is_brand_identity(result: ParseResult) -> bool:
    if (
        not result.normalized_brand
        or not result.normalized_brand.id
        or not result.product_name_text
    ):
        return False
    name_key = _catalog_scent_key(result.product_name_text)
    if not name_key:
        return False
    brand_key = _catalog_scent_key(result.normalized_brand.name)
    if name_key == brand_key:
        return True
    alias_keys = {
        _catalog_scent_key(alias)
        for alias in BrandAlias.objects.filter(
            brand_id=result.normalized_brand.id,
            active=True,
        ).values_list("normalized_alias", flat=True)
        if alias
    }
    return name_key in alias_keys


def _apply_self_titled_catalog_name(result: ParseResult) -> None:
    if not result.normalized_brand or not result.normalized_brand.id:
        return
    if result.product_name_text and not _product_name_is_brand_identity(result):
        return
    brand_key = _catalog_scent_key(result.normalized_brand.name)
    candidates = []
    for perfume in Perfume.objects.filter(brand_id=result.normalized_brand.id).only(
        "name",
        "concentration",
        "audience",
        "collection_name",
    ):
        if not _catalog_perfume_matches_parse_context(perfume, result):
            continue
        name_key = _catalog_scent_key(perfume.name)
        if not name_key or name_key == brand_key:
            candidates.append(perfume)
    if len(candidates) == 1:
        result.product_name_text = (
            candidates[0].name.strip() or result.normalized_brand.name
        )
    elif len(candidates) > 1:
        result.modifiers.append(MANUAL_REVIEW_MODIFIER)
        result.warnings.append("self-titled catalogue match ambiguous")


def _release_year_context_terms() -> set[str]:
    terms = {
        "edp",
        "edt",
        "edc",
        "parfum",
        "perfume",
        "eau",
        "de",
        "toilette",
        "cologne",
        "extrait",
        "ml",
        "мл",
        "tester",
        "test",
        "sample",
        "travel",
        "mini",
        "set",
        "refill",
        "woodbox",
        "wood",
        "box",
        "тестер",
        "тест",
        "пробник",
        "мини",
        "набор",
    }
    terms.update(_tester_terms())
    terms.update(_sample_terms())
    terms.update(_travel_terms())
    terms.update(_mini_terms())
    terms.update(_set_terms())
    terms.update(_refill_terms())
    for (
        _supplier_id,
        needle,
        _value,
        is_regex,
        *_rest,
    ) in get_concentration_alias_rows():
        if not is_regex:
            terms.update(normalize_text(needle).split())
    return {term for term in terms if term}


def _is_name_word(token: str, context_terms: set[str]) -> bool:
    normalized = normalize_text(token)
    return bool(
        normalized
        and re.search(r"[a-zа-я]", normalized)
        and normalized not in context_terms
    )


def _release_year_is_name_bearing(
    text: str, match: re.Match, context_terms: set[str]
) -> bool:
    before = text[: match.start()].strip().split()
    after = text[match.end() :].strip().split()
    previous_token = before[-1] if before else ""
    next_token = after[0] if after else ""
    return _is_name_word(previous_token, context_terms) and _is_name_word(
        next_token, context_terms
    )


def _catalog_collection_for_identity(
    brand: Brand | None, product_name_text: str, concentration: str
) -> str:
    if not brand or not product_name_text or not concentration:
        return ""
    target_name = normalize_text(product_name_text)
    matches = []
    for perfume in Perfume.objects.filter(
        brand=brand,
        concentration__iexact=concentration,
    ).exclude(collection_name=""):
        if normalize_text(perfume.name) == target_name:
            matches.append(perfume.collection_name)
    collections = {collection for collection in matches if collection}
    if len(collections) == 1:
        return next(iter(collections))
    return ""


def _extract_release_year(text: str) -> tuple[int | None, str]:
    matches = list(re.finditer(r"(?<!\d)(?P<year>(?:19|20)\d{2})(?!\d)", text))
    if not matches:
        return None, text
    context_terms = _release_year_context_terms()
    match = next(
        (
            candidate
            for candidate in reversed(matches)
            if not _release_year_is_name_bearing(text, candidate, context_terms)
        ),
        None,
    )
    if not match:
        return None, text
    year = int(match.group("year"))
    return (
        year,
        re.sub(r"\s+", " ", f"{text[:match.start()]} {text[match.end():]}").strip(),
    )


def _name_bearing_modifiers(product_alias: ProductAlias) -> set[str]:
    alias_identity = normalize_text(
        " ".join([product_alias.alias_text, product_alias.canonical_text])
    )
    return {
        modifier
        for modifier in MODIFIER_TERMS
        if _contains_phrase(alias_identity, normalize_text(modifier))
    }


def _modifiers_from_name_bearing_phrases(product_name_text: str) -> set[str]:
    normalized_name = normalize_text(product_name_text)
    modifiers: set[str] = set()
    for phrase in NAME_BEARING_MODIFIER_PHRASES:
        if _contains_phrase(normalized_name, phrase):
            modifiers.update(
                modifier
                for modifier in MODIFIER_TERMS
                if _contains_phrase(phrase, normalize_text(modifier))
            )
    return modifiers


def _extract_packaging_descriptor(text: str) -> tuple[str, list[str]]:
    terms: list[str] = []
    packaging_parts: list[str] = []

    def add_packaging(value: str) -> None:
        if value and value not in packaging_parts:
            packaging_parts.append(value)

    for term in NEW_DESIGN_PACKAGING_TERMS:
        normalized = normalize_text(term)
        if _contains_phrase(text, normalized):
            terms.append(normalized)
            add_packaging("new_design")

    for term in _old_design_packaging_terms():
        normalized = normalize_text(term)
        if _contains_phrase(text, normalized):
            terms.append(normalized)
            add_packaging("old_design")

    for term in _with_cap_packaging_terms():
        normalized = normalize_text(term)
        if _contains_phrase(text, normalized):
            terms.append(normalized)
            add_packaging("with_cap")

    for term in _dented_packaging_terms():
        normalized = normalize_text(term)
        if _contains_phrase(text, normalized):
            terms.append(normalized)
            add_packaging("dented")

    for term in _refillable_packaging_terms():
        normalized = normalize_text(term)
        if _contains_phrase(text, normalized):
            terms.append(normalized)
            add_packaging("refillable")

    for term in [*NO_BOX_TERMS, *WOODBOX_TERMS, *GRAY_BOX_TERMS]:
        normalized = normalize_text(term)
        if not _contains_phrase(text, normalized):
            continue
        terms.append(normalized)
        if normalized in {normalize_text(value) for value in NO_BOX_TERMS}:
            add_packaging("no_box")
        elif normalized in {normalize_text(value) for value in WOODBOX_TERMS}:
            add_packaging("woodbox")
        else:
            add_packaging("gray_box")

    if "new_design" in packaging_parts or "old_design" in packaging_parts:
        for term in GRAY_BOX_COLOR_TERMS:
            normalized = normalize_text(term)
            if _contains_phrase(text, normalized):
                terms.append(normalized)
                add_packaging("gray_box")

    if "gray_box" in packaging_parts:
        packaging_parts = [
            part for part in packaging_parts if part not in {"new_design", "old_design"}
        ]

    unique_terms: list[str] = []
    seen: set[str] = set()
    for term in sorted(terms, key=len, reverse=True):
        if term and term not in seen:
            seen.add(term)
            unique_terms.append(term)
    return " ".join(packaging_parts)[:80], unique_terms


def _audience_terms_to_strip(
    audience_aliases: tuple[tuple[str, str, str], ...],
) -> list[str]:
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
        value = Decimal(multi_pack_match.group("size").replace(",", ".")).quantize(
            Decimal("0.01")
        )
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
    reversed_ml_match = re.search(
        r"\b(?:ml|мл|м\.л\.?)\s*(\d+(?:[.,]\d+)?)(?=\s|$)", text
    )
    if reversed_ml_match:
        raw = reversed_ml_match.group(0)
        value = Decimal(reversed_ml_match.group(1).replace(",", ".")).quantize(
            Decimal("0.01")
        )
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
    trailing_terms = tuple(
        {
            *trailing_terms,
            *_tester_terms(),
            *_sample_terms(),
            *_travel_terms(),
            *_mini_terms(),
            *_set_terms(),
            *_refill_terms(),
            *_variant_type_terms(),
        }
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


def _brand_alias_pattern(alias: BrandAlias) -> str:
    return normalize_alias_value(alias.normalized_alias or alias.alias_text)


def _brand_alias_match(alias: BrandAlias, text: str):
    pattern = _brand_alias_pattern(alias)
    if not pattern:
        return None
    alias_text = normalize_alias_value(text)
    if alias.is_regex:
        return _safe_regex_search(pattern, alias_text, alias=alias)
    return re.search(rf"(^|\s){re.escape(pattern)}($|\s)", alias_text)


def _brand_name_match(brand: Brand, text: str):
    brand_text = normalize_text(brand.name)
    if not brand_text:
        return None
    return re.search(rf"(^|\s){re.escape(brand_text)}($|\s)", text)


def _generated_brand_alias_candidates(brand: Brand) -> set[str]:
    tokens = normalize_text(brand.name).split()
    tokens = [token for token in tokens if re.search(r"[a-z0-9а-я]", token)]
    if len(tokens) < 2:
        return set()
    first, *rest = tokens
    rest_text = " ".join(rest)
    compact_rest = "".join(rest)
    aliases = {
        f"{first[:1]}.{rest_text}",
        f"{first[:1]}. {rest_text}",
        f"{first[:1]} {rest_text}",
    }
    if compact_rest != rest_text:
        aliases.add(f"{first[:1]}.{compact_rest}")
    if (
        len(tokens) == 2
        and len(rest[0]) >= 4
        and rest[0] not in GENERATED_BRAND_ALIAS_STOPWORDS
        and not first.startswith(rest[0][:4])
        and not rest[0].startswith(first[:4])
    ):
        aliases.add(rest[0])
    return {alias for alias in aliases if len(alias) >= 4}


def _generated_brand_alias_entries(brands: list[Brand]) -> list[tuple[str, Brand]]:
    full_brand_names = {normalize_text(brand.name): brand.id for brand in brands}
    alias_brand_ids: dict[str, set[int]] = {}
    alias_brands: dict[tuple[str, int], Brand] = {}
    for brand in brands:
        for alias in _generated_brand_alias_candidates(brand):
            alias_brand_ids.setdefault(alias, set()).add(brand.id)
            alias_brands[(alias, brand.id)] = brand

    entries: list[tuple[str, Brand]] = []
    for alias, brand_ids in alias_brand_ids.items():
        if len(brand_ids) != 1:
            continue
        brand_id = next(iter(brand_ids))
        if alias in full_brand_names and full_brand_names[alias] != brand_id:
            continue
        entries.append((alias, alias_brands[(alias, brand_id)]))
    return sorted(entries, key=lambda item: (-len(item[0]), item[0]))


def _generated_brand_alias_match(alias_text: str, text: str):
    pattern = re.escape(alias_text).replace(r"\.", r"\.\s*")
    return re.search(rf"(^|\s){pattern}($|\s)", text)


def _ordered_brand_aliases(aliases: list[BrandAlias]) -> list[BrandAlias]:
    return sorted(
        aliases,
        key=lambda alias: (
            alias.priority,
            -len(_brand_alias_pattern(alias)),
            alias.alias_text.lower(),
        ),
    )


def _match_aliases(text: str, supplier_id: int | None):
    aliases = list(
        BrandAlias.objects.filter(active=True)
        .select_related("brand")
        .order_by("supplier_id", "priority", "-normalized_alias")
    )
    supplier_aliases = [
        alias
        for alias in aliases
        if supplier_id is not None and alias.supplier_id == supplier_id
    ]
    global_aliases = [alias for alias in aliases if alias.supplier_id is None]

    for alias in _ordered_brand_aliases(supplier_aliases) + _ordered_brand_aliases(
        global_aliases
    ):
        match = _brand_alias_match(alias, text)
        if match and match.start() == 0:
            return alias, alias.brand

    brands = sorted(
        Brand.objects.filter(is_active=True),
        key=lambda brand: (-len(normalize_text(brand.name)), brand.name.lower()),
    )
    generated_brand_aliases = _generated_brand_alias_entries(brands)
    for brand in brands:
        match = _brand_name_match(brand, text)
        if match and match.start() == 0:
            return None, brand

    for alias_text, brand in generated_brand_aliases:
        match = _generated_brand_alias_match(alias_text, text)
        if match and match.start() == 0:
            return alias_text, brand

    for alias in _ordered_brand_aliases(supplier_aliases) + _ordered_brand_aliases(
        global_aliases
    ):
        if _brand_alias_match(alias, text):
            return alias, alias.brand

    for brand in brands:
        if _brand_name_match(brand, text):
            return None, brand
    for alias_text, brand in generated_brand_aliases:
        if _generated_brand_alias_match(alias_text, text):
            return alias_text, brand
    return None, None


def _match_initial_brand_name(text: str) -> Brand | None:
    brands = sorted(
        Brand.objects.all(),
        key=lambda brand: (-len(normalize_text(brand.name)), brand.name.lower()),
    )
    for brand in brands:
        match = _brand_name_match(brand, text)
        if match and match.start() == 0:
            return brand
    return None


def parse_supplier_product(product: SupplierProduct) -> ParseResult:
    raw = product.name or ""
    text = normalize_text(" ".join([product.brand or "", raw, product.size or ""]))
    result = ParseResult(raw_name=raw, normalized_text=text)
    initial_alias, initial_brand = _match_aliases(text, product.supplier_id)
    initial_direct_brand = _match_initial_brand_name(text)
    is_bag = _contains_any_phrase(text, _bag_terms())
    is_cosmetic_poudre = _contains_any_phrase(text, _cosmetic_poudre_terms())
    is_deodorant_candidate = _contains_any_phrase(text, _deodorant_terms())
    is_decant = _contains_any_phrase(text, _decant_terms())
    is_vintage = _contains_any_phrase(text, _vintage_terms())
    is_non_perfume = is_bag or is_cosmetic_poudre

    garbage_keyword = match_trailing_garbage_marker(raw) or match_garbage_keyword(text)
    structured_non_garbage_keywords = {
        normalize_text(term) for term in _structured_packaging_terms()
    }
    structured_non_garbage_keywords.update(_vintage_terms())
    if (
        garbage_keyword
        and normalize_text(garbage_keyword) not in structured_non_garbage_keywords
    ):
        result.modifiers = [GARBAGE_MODIFIER]
        result.warnings = [f"{GARBAGE_WARNING_PREFIX}: {garbage_keyword}"]
        result.confidence = 100
        return result

    if not is_non_perfume:
        size, raw_size, text = _extract_size(text)
        result.size_ml = size
        result.raw_size_text = raw_size
        result.release_year, text = _extract_release_year(text)

        concentration_alias_rows = get_concentration_alias_rows()
        supplier_aliases = [
            row for row in concentration_alias_rows if row[0] == product.supplier_id
        ]
        global_aliases = [row for row in concentration_alias_rows if row[0] is None]
        applicable_concentration_aliases = supplier_aliases + global_aliases
        for row in applicable_concentration_aliases:
            _, needle, value, is_regex, *rest = row
            alias_id = rest[0] if rest else None
            if is_regex:
                alias = (
                    ConcentrationAlias.objects.filter(pk=alias_id).first()
                    if alias_id
                    else None
                )
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
    is_deodorant = is_deodorant_candidate and not result.concentration
    is_non_perfume = is_non_perfume or is_deodorant or is_decant or is_vintage

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
    decoded_terms = _decoded_terms()
    supplier_comment_terms = _supplier_comment_terms()
    cosmetic_poudre_terms = _cosmetic_poudre_terms()
    deodorant_terms = _deodorant_terms()
    decant_terms = _decant_terms()
    vintage_terms = _vintage_terms()
    detected_packaging, packaging_descriptor_terms = _extract_packaging_descriptor(text)
    variant_type_aliases = _variant_type_aliases()
    variant_type_terms = tuple(alias for alias, _variant_type in variant_type_aliases)

    result.is_tester = _contains_any_phrase(text, tester_terms)
    result.is_sample = _contains_any_phrase(text, sample_terms)
    result.is_travel = _contains_any_phrase(text, travel_terms)
    is_mini = _contains_any_phrase(text, mini_terms)
    result.is_set = _contains_any_phrase(text, set_terms)
    is_decoded = _contains_any_phrase(text, decoded_terms)
    custom_variant_type = ""
    for alias_text, variant_type in variant_type_aliases:
        if _contains_phrase(text, alias_text):
            custom_variant_type = variant_type
            break
    if result.raw_size_text and "*" in result.raw_size_text:
        result.is_set = True
    if detected_packaging:
        result.packaging = detected_packaging
    else:
        result.packaging = ""
    result.variant_type = (
        "sample"
        if result.is_sample
        else (
            "travel"
            if result.is_travel
            else (
                "mini"
                if is_mini
                else (
                    "set"
                    if result.is_set
                    else (
                        "decoded"
                        if is_decoded
                        else (
                            custom_variant_type
                            or ("tester" if result.is_tester else "standard")
                        )
                    )
                )
            )
        )
    )
    if is_bag:
        result.variant_type = BAG_MODIFIER
    elif is_cosmetic_poudre:
        result.variant_type = "poudre"
    elif is_deodorant:
        result.variant_type = DEODORANT_MODIFIER
    elif is_decant:
        result.variant_type = DECANT_MODIFIER
    elif is_vintage:
        result.variant_type = VINTAGE_MODIFIER
    result.modifiers = [
        term
        for term in MODIFIER_TERMS
        if re.search(rf"(^|\s){re.escape(term)}($|\s)", text)
    ]
    if is_bag:
        result.modifiers.append(BAG_MODIFIER)
    if is_cosmetic_poudre:
        result.modifiers.append(COSMETIC_PUDRE_MODIFIER)
    if is_deodorant:
        result.modifiers.append(DEODORANT_MODIFIER)
    if is_decant:
        result.modifiers.append(DECANT_MODIFIER)
    if is_vintage:
        result.modifiers.append(VINTAGE_MODIFIER)
    if is_mini and MINI_MODIFIER not in result.modifiers:
        result.modifiers.append(MINI_MODIFIER)
    if _contains_any_phrase(text, refill_terms):
        result.modifiers.append(REFILL_MODIFIER)

    alias, brand = _match_aliases(text, product.supplier_id)
    if not brand and initial_brand:
        alias, brand = initial_alias, initial_brand
    if not brand and initial_direct_brand:
        alias, brand = None, initial_direct_brand
    if brand:
        result.normalized_brand = brand
        result.detected_brand_text = (
            alias
            if isinstance(alias, str)
            else (alias.alias_text if alias else brand.name)
        )
        text = _strip_first_phrase(text, result.detected_brand_text)
        if alias and not isinstance(alias, str) and alias.supplier_id:
            result.warnings.append("supplier-specific alias overrode global alias")

    if not is_non_perfume and not result.size_ml:
        size, raw_size, compact_text = _extract_loose_trailing_size(text)
        if size is not None:
            result.size_ml = size
            result.raw_size_text = raw_size
            text = compact_text

    product_alias_non_name_terms = [
        *tester_terms,
        *sample_terms,
        *travel_terms,
        *mini_terms,
        *set_terms,
        *cosmetic_poudre_terms,
        *deodorant_terms,
        *decant_terms,
        *vintage_terms,
        *refill_terms,
        *decoded_terms,
        *supplier_comment_terms,
        *packaging_descriptor_terms,
        *variant_type_terms,
        *NO_BOX_TERMS,
        *WOODBOX_TERMS,
    ]
    product_alias_match_text = _strip_known_terms(
        text,
        [
            *_audience_terms_to_strip(audience_aliases),
            *product_alias_non_name_terms,
        ],
        preserve_phrases=NAME_AUDIENCE_TERMS,
    )
    raw_product_alias_match_text = _strip_known_terms(
        text, product_alias_non_name_terms
    )
    product_aliases = ProductAlias.objects.filter(active=True).order_by(
        "supplier_id", "priority", "-alias_text"
    )
    if result.normalized_brand:
        product_aliases = product_aliases.filter(
            Q(brand_id=result.normalized_brand.id) | Q(brand__isnull=True)
        )
    for product_alias in list(
        product_aliases.filter(supplier_id=product.supplier_id)
    ) + list(product_aliases.filter(supplier__isnull=True)):
        alias_text = normalize_text(product_alias.alias_text)
        excluded_terms = _split_terms(product_alias.excluded_terms)
        alias_match_context = ""
        if alias_text:
            if _contains_phrase(product_alias_match_text, alias_text):
                alias_match_context = product_alias_match_text
            elif _contains_phrase(raw_product_alias_match_text, alias_text):
                alias_match_context = raw_product_alias_match_text
        if alias_match_context and not any(
            _contains_phrase(text, term) for term in excluded_terms
        ):
            if not result.normalized_brand and product_alias.brand_id:
                result.normalized_brand = product_alias.brand
                result.detected_brand_text = product_alias.alias_text
            if product_alias.collection_name:
                result.collection_name = product_alias.collection_name
            if not product_alias.canonical_text:
                stripped_text = _strip_known_terms(text, [alias_text])
                stripped_match_text = _strip_known_terms(
                    alias_match_context, [alias_text]
                )
                if stripped_match_text:
                    text = stripped_text
                    product_alias_match_text = _strip_known_terms(
                        product_alias_match_text, [alias_text]
                    )
                continue
            result.product_name_text = _product_name_from_alias_match_context(
                alias_match_context,
                alias_text,
                product_alias.canonical_text,
            )
            remaining_name = _remaining_after_alias_prefix(
                alias_match_context, alias_text
            )
            if remaining_name and result.concentration and result.size_ml:
                result.product_name_text = _clean_product_name_text(
                    f"{result.product_name_text} {remaining_name}".strip()
                )
            name_modifiers = _name_bearing_modifiers(product_alias)
            if name_modifiers:
                result.modifiers = [
                    modifier
                    for modifier in result.modifiers
                    if modifier not in name_modifiers
                ]
            if (
                product_alias.concentration
                and result.concentration
                and product_alias.supplier_id == product.supplier_id
            ):
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
                *cosmetic_poudre_terms,
                *deodorant_terms,
                *decant_terms,
                *vintage_terms,
                *refill_terms,
                *decoded_terms,
                *supplier_comment_terms,
                *packaging_descriptor_terms,
                *variant_type_terms,
                *NO_BOX_TERMS,
                *WOODBOX_TERMS,
            ],
            preserve_phrases=NAME_AUDIENCE_TERMS,
        )
        result.product_name_text = _clean_product_name_text(remaining)

    result.product_name_text = _clean_product_name_text(result.product_name_text)
    if not result.product_name_text:
        audience_identity_text = _strip_known_terms(
            text,
            [
                result.raw_size_text,
                result.concentration,
                *product_alias_non_name_terms,
            ],
        )
        result.product_name_text = _catalog_name_from_audience_only_context(
            result,
            audience_identity_text,
            audience_aliases,
        )
    result.product_name_text = _strip_trailing_supplier_status_garbage(result)
    result.product_name_text = _strip_cyrillic_name_tokens_for_latin_brand(result)
    result.product_name_text = _strip_redundant_trailing_audience_suffix(result)
    _apply_self_titled_catalog_name(result)
    result.product_name_text = _canonicalize_product_name_from_catalog(result)
    if not result.collection_name:
        result.collection_name = _catalog_collection_for_identity(
            result.normalized_brand,
            result.product_name_text,
            result.concentration,
        )

    if not result.normalized_brand:
        result.warnings.append("brand missing")
    if not result.product_name_text:
        result.warnings.append("product name missing")
    name_bearing_modifiers = _modifiers_from_name_bearing_phrases(
        result.product_name_text
    )
    if name_bearing_modifiers:
        result.modifiers = [
            modifier
            for modifier in result.modifiers
            if modifier not in name_bearing_modifiers
        ]
    if not is_non_perfume and not result.concentration:
        result.warnings.append("concentration missing")
    if not is_non_perfume and not result.size_ml:
        result.warnings.append("size ambiguous")
    if not is_non_perfume and not result.supplier_gender_hint:
        result.warnings.append("gender missing")
    for modifier in result.modifiers:
        if modifier in {
            BAG_MODIFIER,
            COSMETIC_PUDRE_MODIFIER,
            DEODORANT_MODIFIER,
            DECANT_MODIFIER,
            VINTAGE_MODIFIER,
            REFILL_MODIFIER,
        }:
            continue
        result.warnings.append(f"{modifier} detected")

    if is_non_perfume:
        result.confidence = min(
            60
            + (25 if result.normalized_brand else 0)
            + (15 if result.product_name_text else 0),
            100,
        )
    else:
        score = 25
        score += 25 if result.normalized_brand else 0
        score += 15 if result.product_name_text else 0
        score += 15 if result.concentration else 0
        score += 15 if result.size_ml else 0
        score += 5 if result.supplier_gender_hint else 0
        result.confidence = min(score, 100)
    result.normalized_text = normalize_text(
        " ".join([result.normalized_text, result.product_name_text])
    )
    return result


def save_parse(
    product: SupplierProduct, *, force: bool = False
) -> ParsedSupplierProduct:
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
            "release_year": parsed.release_year,
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
    _apply_catalog_conflict_manual_review(obj)
    from assistant_linking.services.normalization_stats import mark_stats_stale

    mark_stats_stale()
    return obj


def _apply_catalog_conflict_manual_review(parsed: ParsedSupplierProduct) -> None:
    if parsed.supplier_product.catalog_perfume_id:
        return
    if not parsed.concentration:
        return
    if parsed.product_category_label != "Perfume":
        return
    from assistant_linking.services.catalog_matcher import candidate_matches

    candidates = candidate_matches(parsed, limit=1)
    if not candidates:
        return
    candidate = candidates[0]
    if candidate.score < 80 or "concentration differs" not in candidate.conflicts:
        return
    suggested = candidate.perfume.concentration
    if not suggested or suggested == parsed.concentration:
        return
    warning = CATALOG_CONCENTRATION_CONFLICT_WARNING.format(
        suggested=suggested,
        parsed=parsed.concentration,
    )
    modifiers = list(parsed.modifiers or [])
    warnings = list(parsed.warnings or [])
    changed = False
    if MANUAL_REVIEW_MODIFIER not in modifiers:
        modifiers.append(MANUAL_REVIEW_MODIFIER)
        changed = True
    if warning not in warnings:
        warnings.append(warning)
        changed = True
    if changed:
        parsed.modifiers = modifiers
        parsed.warnings = warnings
        parsed.save(update_fields=["modifiers", "warnings", "updated_at"])
