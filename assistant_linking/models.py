import re

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models

from assistant_linking.utils.text import normalize_alias_value
from assistant_linking.utils.text import fold_latin_diacritics
from catalog.models import Brand, compact_decimal_text, get_or_create_collection


CONCENTRATION_ALIAS_CACHE_KEY = "assistant_linking:concentration_aliases:v1"
BAG_MODIFIER = "bag"
COSMETIC_PUDRE_MODIFIER = "cosmetic_poudre"
DEODORANT_MODIFIER = "deodorant"
DECANT_MODIFIER = "decant"
VINTAGE_MODIFIER = "vintage"
ATOMIZER_MODIFIER = "atomizer"
MANUAL_REVIEW_MODIFIER = "manual_review"
GARBAGE_MODIFIER = "garbage"
PERFUME_CATEGORY_CONCENTRATIONS = {
    "Eau de Parfum",
    "Eau de Toilette",
    "Eau de Cologne",
    "Parfum",
    "Extrait de Parfum",
    "Perfume Oil",
}
HAIR_CARE_CATEGORY_CONCENTRATIONS = {"Hair Mist", "Hair Perfume"}
REDOS_REGEX_SHAPES = (r"(.+)+", r"(.*)*", r"(.+)*", r"(\w+)+")
TITLECASE_LOWER_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "de",
    "del",
    "della",
    "des",
    "di",
    "du",
    "el",
    "en",
    "for",
    "from",
    "in",
    "la",
    "le",
    "les",
    "of",
    "on",
    "or",
    "the",
    "to",
    "van",
    "von",
    "with",
}
TITLECASE_APOSTROPHE_SUFFIXES = {"d", "ll", "m", "re", "s", "t", "ve"}


def strip_leading_fragrantica_brand_name(brand_name: str, scent_name: str) -> str:
    scent = re.sub(r"\s+", " ", (scent_name or "").strip())
    brand = re.sub(r"\s+", " ", (brand_name or "").strip())
    if not scent or not brand:
        return scent
    pattern = re.compile(
        rf"^{re.escape(brand)}(?:[\s/\-:]+)",
        flags=re.IGNORECASE,
    )
    match = pattern.match(scent)
    if not match:
        return scent
    cleaned = scent[match.end() :].strip()
    return cleaned or scent


def normalized_fragrantica_product_name(brand_name: str, scent_name: str) -> str:
    text = fold_latin_diacritics(
        strip_leading_fragrantica_brand_name(brand_name, scent_name),
    )
    return normalize_alias_value(text).replace("&", "and")


def normalized_fragrantica_brand_name(value: str) -> str:
    text = fold_latin_diacritics(value).replace("&", " and ")
    return normalize_alias_value(text)


def display_label(value: str, *, default: str = "") -> str:
    text = (value or default or "").replace("_", " ").strip()
    if not text:
        return ""
    return " ".join(part[:1].upper() + part[1:] for part in text.split())


def display_title(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    def title_piece(piece: str, *, lower_allowed: bool) -> str:
        if not piece:
            return piece
        if lower_allowed and piece.lower() in TITLECASE_LOWER_WORDS:
            return piece.lower()
        return piece[:1].upper() + piece[1:]

    words = []
    for index, word in enumerate(text.split()):
        lower_allowed = index > 0 or (
            word[:1].islower() and word.lower() in TITLECASE_LOWER_WORDS
        )
        hyphen_parts = []
        for hyphen_part in word.split("-"):
            apostrophe_parts = hyphen_part.split("'")
            hyphen_parts.append(
                "'".join(
                    (
                        part.lower()
                        if sub_index > 0
                        and part.lower() in TITLECASE_APOSTROPHE_SUFFIXES
                        else title_piece(
                            part, lower_allowed=lower_allowed and sub_index == 0
                        )
                    )
                    for sub_index, part in enumerate(apostrophe_parts)
                )
            )
        words.append("-".join(hyphen_parts))
    return " ".join(words)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BrandAlias(TimeStampedModel):
    brand = models.ForeignKey(
        "catalog.Brand", on_delete=models.CASCADE, related_name="aliases", db_index=True
    )
    alias_text = models.CharField(max_length=255, db_index=True)
    normalized_alias = models.CharField(max_length=255, db_index=True)
    supplier = models.ForeignKey(
        "prices.Supplier",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="brand_aliases",
        db_index=True,
    )
    active = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=100, db_index=True)
    is_regex = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("supplier__name", "priority", "alias_text")
        constraints = [
            models.UniqueConstraint(
                fields=["alias_text", "supplier", "brand"],
                name="uniq_assistant_brand_alias",
            )
        ]

    def __str__(self) -> str:
        scope = self.supplier.name if self.supplier_id else "global"
        return f"{self.alias_text} -> {self.brand} ({scope})"

    def clean(self):
        super().clean()
        if not self.normalized_alias and self.alias_text:
            self.normalized_alias = normalize_alias_value(self.alias_text)
        pattern = self.normalized_alias or self.alias_text
        if self.is_regex and pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValidationError({"normalized_alias": f"Invalid regex: {exc}"})
            if len(pattern) > 200:
                raise ValidationError(
                    {"normalized_alias": "Pattern too long (max 200 chars)."}
                )
            for bad in REDOS_REGEX_SHAPES:
                if bad in pattern:
                    raise ValidationError(
                        {
                            "normalized_alias": (
                                f"Pattern contains catastrophic-backtracking shape: {bad}"
                            )
                        }
                    )

    def save(self, *args, **kwargs):
        if not self.normalized_alias:
            self.normalized_alias = normalize_alias_value(self.alias_text)
        self.full_clean()
        return super().save(*args, **kwargs)


class ProductAlias(TimeStampedModel):
    perfume = models.ForeignKey(
        "catalog.Perfume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_aliases",
        db_index=True,
    )
    brand = models.ForeignKey(
        "catalog.Brand",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_aliases",
        db_index=True,
    )
    alias_text = models.CharField(max_length=255, db_index=True)
    canonical_text = models.CharField(max_length=255, db_index=True)
    collection_name = models.CharField(max_length=180, blank=True, db_index=True)
    supplier = models.ForeignKey(
        "prices.Supplier",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="product_aliases",
        db_index=True,
    )
    concentration = models.CharField(max_length=80, blank=True)
    audience = models.CharField(max_length=80, blank=True)
    excluded_terms = models.TextField(blank=True)
    active = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=100, db_index=True)

    class Meta:
        ordering = ("supplier__name", "priority", "alias_text")

    def __str__(self) -> str:
        return f"{self.alias_text} -> {self.canonical_text}"


class FragranticaProduct(TimeStampedModel):
    STATUS_UNLINKED = "unlinked"
    STATUS_LINKED = "linked"
    STATUS_IGNORED = "ignored"
    STATUS_CHOICES = (
        (STATUS_UNLINKED, "Unlinked"),
        (STATUS_LINKED, "Linked"),
        (STATUS_IGNORED, "Ignored"),
    )

    brand_name = models.CharField(max_length=200, db_index=True)
    normalized_brand_name = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=220, db_index=True)
    normalized_name = models.CharField(max_length=255, db_index=True)
    collection = models.ForeignKey(
        "catalog.Collection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fragrantica_products",
        db_index=True,
    )
    collection_name = models.CharField(max_length=180, blank=True, db_index=True)
    audience = models.CharField(max_length=80, blank=True, db_index=True)
    release_year = models.PositiveSmallIntegerField(
        null=True, blank=True, db_index=True
    )
    source_path = models.CharField(max_length=500, blank=True)
    source_url = models.URLField(blank=True)
    source_domain = models.CharField(
        max_length=160, default="fragrantica.com", db_index=True
    )
    matched_perfume = models.ForeignKey(
        "catalog.Perfume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fragrantica_products",
        db_index=True,
    )
    match_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_UNLINKED,
        db_index=True,
    )

    class Meta:
        ordering = ("brand_name", "collection_name", "name")
        indexes = [
            models.Index(
                fields=["normalized_brand_name", "match_status", "normalized_name"],
                name="alink_frag_bstat_name_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_brand_name", "normalized_name", "source_path"],
                name="uniq_fragrantica_product_source",
            )
        ]

    def save(self, *args, **kwargs):
        self.normalized_brand_name = normalized_fragrantica_brand_name(self.brand_name)
        self.normalized_name = normalized_fragrantica_product_name(
            self.brand_name,
            self.name,
        )
        if self.collection_id:
            self.collection_name = self.collection.name
        elif self.collection_name:
            brand = Brand.objects.filter(name__iexact=self.brand_name).first()
            if brand:
                self.collection = get_or_create_collection(brand, self.collection_name)
        if self.matched_perfume_id and self.match_status == self.STATUS_UNLINKED:
            self.match_status = self.STATUS_LINKED
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.brand_name} / {self.name}"

    @property
    def source_href(self) -> str:
        if self.source_url:
            return self.source_url
        if self.source_path.startswith(("http://", "https://")):
            return self.source_path
        if self.source_path.startswith("/"):
            return f"https://www.fragrantica.com{self.source_path}"
        return self.source_path


class FragranticaProductLink(TimeStampedModel):
    LINK_TYPE_PRIMARY = "primary"
    LINK_TYPE_MANUAL_EXTRA = "manual_extra"
    LINK_TYPE_CHOICES = (
        (LINK_TYPE_PRIMARY, "Primary"),
        (LINK_TYPE_MANUAL_EXTRA, "Manual extra"),
    )

    source = models.ForeignKey(
        FragranticaProduct,
        on_delete=models.CASCADE,
        related_name="review_links",
        db_index=True,
    )
    perfume = models.ForeignKey(
        "catalog.Perfume",
        on_delete=models.CASCADE,
        related_name="fragrantica_review_links",
        db_index=True,
    )
    link_type = models.CharField(
        max_length=20,
        choices=LINK_TYPE_CHOICES,
        default=LINK_TYPE_MANUAL_EXTRA,
        db_index=True,
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("source__brand_name", "source__collection_name", "source__name")
        constraints = [
            models.UniqueConstraint(
                fields=["source", "perfume"],
                name="uniq_fragrantica_product_review_link",
            )
        ]
        indexes = [
            models.Index(fields=["perfume", "link_type"]),
            models.Index(fields=["source", "link_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.source} -> {self.perfume}"


class ConcentrationAlias(TimeStampedModel):
    concentration = models.CharField(max_length=80, db_index=True)
    alias_text = models.CharField(max_length=255, db_index=True)
    normalized_alias = models.CharField(max_length=255, db_index=True)
    supplier = models.ForeignKey(
        "prices.Supplier",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="concentration_aliases",
        db_index=True,
    )
    active = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=100, db_index=True)
    is_regex = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("supplier__name", "priority", "alias_text")
        constraints = [
            models.UniqueConstraint(
                fields=["alias_text", "supplier", "concentration"],
                name="uniq_assistant_concentration_alias",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.normalized_alias:
            self.normalized_alias = normalize_alias_value(self.alias_text)
        super().save(*args, **kwargs)
        cache.delete(CONCENTRATION_ALIAS_CACHE_KEY)

    def delete(self, *args, **kwargs):
        cache.delete(CONCENTRATION_ALIAS_CACHE_KEY)
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        scope = self.supplier.name if self.supplier_id else "global"
        return f"{self.alias_text} -> {self.concentration} ({scope})"


class ParsedSupplierProduct(TimeStampedModel):
    supplier_product = models.OneToOneField(
        "prices.SupplierProduct",
        on_delete=models.CASCADE,
        related_name="assistant_parse",
    )
    raw_name = models.TextField()
    normalized_text = models.TextField(db_index=True)
    detected_brand_text = models.CharField(max_length=255, blank=True)
    normalized_brand = models.ForeignKey(
        "catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, db_index=True
    )
    product_name_text = models.CharField(max_length=255, blank=True, db_index=True)
    collection_name = models.CharField(max_length=180, blank=True, db_index=True)
    concentration = models.CharField(max_length=80, blank=True, db_index=True)
    size_ml = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True, db_index=True
    )
    raw_size_text = models.CharField(max_length=80, blank=True)
    release_year = models.PositiveSmallIntegerField(
        null=True, blank=True, db_index=True
    )
    supplier_gender_hint = models.CharField(max_length=80, blank=True, db_index=True)
    packaging = models.CharField(max_length=80, blank=True, db_index=True)
    variant_type = models.CharField(max_length=80, blank=True, db_index=True)
    is_tester = models.BooleanField(default=False, db_index=True)
    is_sample = models.BooleanField(default=False, db_index=True)
    is_travel = models.BooleanField(default=False, db_index=True)
    is_set = models.BooleanField(default=False, db_index=True)
    modifiers = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    confidence = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_complete_parse = models.BooleanField(default=False)
    parser_version = models.CharField(max_length=40, default="deterministic-v1")
    locked_by_human = models.BooleanField(default=False, db_index=True)
    last_parsed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("supplier_product__supplier__name", "supplier_product__name")
        indexes = [
            GinIndex(fields=["modifiers"], name="alink_parse_modifiers_gin"),
            models.Index(
                fields=[
                    "normalized_brand",
                    "concentration",
                    "size_ml",
                    "supplier_product",
                ],
                name="alink_parse_complete_page_idx",
                condition=(
                    ~models.Q(product_name_text="")
                    & ~models.Q(concentration="")
                    & models.Q(size_ml__isnull=False)
                    & models.Q(is_set=False)
                ),
            ),
            models.Index(
                fields=["supplier_product", "id"],
                name="alink_parse_complete_sp_idx",
                condition=models.Q(is_complete_parse=True),
            ),
            models.Index(
                fields=["is_complete_parse", "supplier_product", "id"],
                name="alink_parse_complete_flag_idx",
            ),
            models.Index(
                fields=["supplier_product", "id"],
                name="alink_parse_missing_brand_idx",
                condition=models.Q(normalized_brand__isnull=True),
            ),
            models.Index(
                fields=["supplier_product", "id"],
                name="alink_parse_missing_name_idx",
                condition=models.Q(product_name_text=""),
            ),
            models.Index(
                fields=["supplier_product", "id"],
                name="alink_parse_missing_conc_idx",
                condition=models.Q(concentration=""),
            ),
            models.Index(
                fields=["supplier_product", "id"],
                name="alink_parse_missing_size_idx",
                condition=models.Q(size_ml__isnull=True),
            ),
        ]

    def compute_is_complete_parse(self) -> bool:
        modifiers = set(self.modifiers or [])
        is_non_perfume = (
            BAG_MODIFIER in modifiers
            or self.variant_type == BAG_MODIFIER
            or COSMETIC_PUDRE_MODIFIER in modifiers
            or self.variant_type == "poudre"
            or DEODORANT_MODIFIER in modifiers
            or self.variant_type == DEODORANT_MODIFIER
            or DECANT_MODIFIER in modifiers
            or self.variant_type == DECANT_MODIFIER
            or VINTAGE_MODIFIER in modifiers
            or self.variant_type == VINTAGE_MODIFIER
            or ATOMIZER_MODIFIER in modifiers
            or self.variant_type == ATOMIZER_MODIFIER
        )
        return bool(
            self.normalized_brand_id
            and self.product_name_text
            and self.concentration
            and self.size_ml is not None
            and not self.is_set
            and GARBAGE_MODIFIER not in modifiers
            and MANUAL_REVIEW_MODIFIER not in modifiers
            and not is_non_perfume
        )

    def save(self, *args, **kwargs):
        self.is_complete_parse = self.compute_is_complete_parse()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"is_complete_parse"}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Parsed: {self.supplier_product}"

    @property
    def display_brand(self) -> str:
        if self.normalized_brand_id:
            return str(self.normalized_brand)
        return self.detected_brand_text

    @property
    def display_size(self) -> str:
        if self.raw_size_text and any(
            separator in self.raw_size_text for separator in {"*", "+"}
        ):
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
        if (
            ATOMIZER_MODIFIER in (self.modifiers or [])
            or self.variant_type == ATOMIZER_MODIFIER
        ):
            return "Atomizer"
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
        if "refill" in (self.modifiers or []):
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
        if (
            ATOMIZER_MODIFIER in (self.modifiers or [])
            or self.variant_type == ATOMIZER_MODIFIER
        ):
            return "Atomizers"
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


class NormalizationStatsSnapshot(TimeStampedModel):
    parser_version = models.CharField(max_length=40, db_index=True)
    scope_key = models.CharField(max_length=80, db_index=True)
    hidden_keywords_hash = models.CharField(max_length=64, blank=True, db_index=True)
    hidden_keywords = models.JSONField(default=list, blank=True)
    parsed_count = models.PositiveIntegerField(default=0)
    unparsed_count = models.PositiveIntegerField(default=0)
    low_confidence_count = models.PositiveIntegerField(default=0)
    missing_brand_count = models.PositiveIntegerField(default=0)
    missing_name_count = models.PositiveIntegerField(default=0)
    missing_concentration_count = models.PositiveIntegerField(default=0)
    missing_size_count = models.PositiveIntegerField(default=0)
    modifier_count = models.PositiveIntegerField(default=0)
    garbage_count = models.PositiveIntegerField(default=0)
    tester_sample_count = models.PositiveIntegerField(default=0)
    set_count = models.PositiveIntegerField(default=0)
    bag_count = models.PositiveIntegerField(default=0)
    cosmetic_count = models.PositiveIntegerField(default=0)
    deodorant_count = models.PositiveIntegerField(default=0)
    decant_count = models.PositiveIntegerField(default=0)
    vintage_count = models.PositiveIntegerField(default=0)
    atomizer_count = models.PositiveIntegerField(default=0)
    manual_review_count = models.PositiveIntegerField(default=0)
    recent_parse_ids = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_stale = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("-generated_at", "-updated_at")
        constraints = [
            models.UniqueConstraint(
                fields=["parser_version", "scope_key"],
                name="uniq_normalization_stats_snapshot_scope",
            )
        ]

    def __str__(self) -> str:
        return f"Normalization stats {self.parser_version} {self.scope_key}"


class MatchGroup(TimeStampedModel):
    STATUS_OPEN = "open"
    STATUS_REVIEWED = "reviewed"
    STATUS_EXCLUDED = "excluded"
    STATUS_CONFLICT = "conflict"
    STATUS_CHOICES = (
        (STATUS_OPEN, "Open"),
        (STATUS_REVIEWED, "Reviewed"),
        (STATUS_EXCLUDED, "Excluded"),
        (STATUS_CONFLICT, "Conflict"),
    )

    group_key = models.CharField(max_length=500, unique=True, db_index=True)
    normalized_brand = models.ForeignKey(
        "catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, db_index=True
    )
    canonical_name = models.CharField(max_length=255, db_index=True)
    collection_name = models.CharField(max_length=180, blank=True, db_index=True)
    concentration = models.CharField(max_length=80, blank=True, db_index=True)
    audience_hint = models.CharField(max_length=80, blank=True, db_index=True)
    size_ml = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True, db_index=True
    )
    packaging = models.CharField(max_length=80, blank=True, db_index=True)
    variant_type = models.CharField(max_length=80, blank=True, db_index=True)
    candidate_perfume = models.ForeignKey(
        "catalog.Perfume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )
    candidate_variant = models.ForeignKey(
        "catalog.PerfumeVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True
    )
    confidence = models.PositiveSmallIntegerField(default=50, db_index=True)

    class Meta:
        ordering = ("status", "-confidence", "canonical_name")

    def __str__(self) -> str:
        return self.group_key

    @property
    def display_size(self) -> str:
        if self.size_ml is None:
            return ""
        return f"{compact_decimal_text(self.size_ml)}ml"

    @property
    def display_variant_type(self) -> str:
        return display_label(self.variant_type, default="Standard")

    @property
    def display_packaging(self) -> str:
        return display_label(self.packaging, default="Standard")

    @property
    def display_canonical_name(self) -> str:
        return display_title(self.canonical_name)

    @property
    def display_identity(self) -> str:
        variant = self.display_variant_type
        packaging = self.display_packaging
        parts = [
            str(self.normalized_brand) if self.normalized_brand_id else "",
            display_title(self.collection_name),
            self.display_canonical_name,
            self.concentration,
            self.display_size,
            variant if variant != "Standard" else "",
            packaging if packaging != "Standard" and packaging != variant else "",
        ]
        return " / ".join(part for part in parts if part)


class MatchGroupItem(models.Model):
    ROLE_MEMBER = "member"
    ROLE_EXCLUDED = "excluded"
    ROLE_SPLIT = "split"
    ROLE_CONFLICT = "conflict"
    ROLE_CHOICES = (
        (ROLE_MEMBER, "Member"),
        (ROLE_EXCLUDED, "Excluded"),
        (ROLE_SPLIT, "Split"),
        (ROLE_CONFLICT, "Conflict"),
    )

    match_group = models.ForeignKey(
        MatchGroup, on_delete=models.CASCADE, related_name="items", db_index=True
    )
    supplier_product = models.ForeignKey(
        "prices.SupplierProduct",
        on_delete=models.CASCADE,
        related_name="assistant_group_items",
        db_index=True,
    )
    parsed_product = models.ForeignKey(
        ParsedSupplierProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER, db_index=True
    )
    match_score = models.PositiveSmallIntegerField(default=50, db_index=True)
    reasoning = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["match_group", "supplier_product"],
                name="uniq_assistant_group_item",
            )
        ]

    def __str__(self) -> str:
        return f"{self.match_group} / {self.supplier_product}"


class ManualLinkDecision(models.Model):
    DECISION_APPROVE_PERFUME = "approve_perfume"
    DECISION_APPROVE_VARIANT = "approve_variant"
    DECISION_REJECT = "reject"
    DECISION_EXCLUDE = "exclude"
    DECISION_CHOICES = (
        (DECISION_APPROVE_PERFUME, "Approve perfume"),
        (DECISION_APPROVE_VARIANT, "Approve variant"),
        (DECISION_REJECT, "Reject"),
        (DECISION_EXCLUDE, "Exclude"),
    )

    supplier_product = models.ForeignKey(
        "prices.SupplierProduct",
        on_delete=models.CASCADE,
        related_name="assistant_decisions",
        db_index=True,
    )
    perfume = models.ForeignKey(
        "catalog.Perfume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )
    variant = models.ForeignKey(
        "catalog.PerfumeVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )
    decision_type = models.CharField(
        max_length=40, choices=DECISION_CHOICES, db_index=True
    )
    reason = models.TextField(blank=True)
    apply_to_similar = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.supplier_product} / {self.decision_type}"


class ManualLinkDecisionAudit(models.Model):
    previous_pk = models.PositiveBigIntegerField(db_index=True)
    previous_decision_json = models.JSONField()
    replaced_by = models.ForeignKey(
        ManualLinkDecision,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replacement_audits",
    )
    replaced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-replaced_at",)

    def __str__(self) -> str:
        return f"ManualLinkDecision#{self.previous_pk} replaced"


class LinkAction(models.Model):
    ACTION_BULK_LINK = "bulk_link"
    ACTION_UNDO_BULK_LINK = "undo_bulk_link"
    ACTION_CHOICES = (
        (ACTION_BULK_LINK, "Bulk link"),
        (ACTION_UNDO_BULK_LINK, "Undo bulk link"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assistant_link_actions",
    )
    action_type = models.CharField(max_length=40, choices=ACTION_CHOICES, db_index=True)
    payload_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(
                fields=["user", "-created_at"], name="alink_action_user_created_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} / {self.action_type} / {self.created_at:%Y-%m-%d %H:%M:%S}"


class LinkSuggestion(TimeStampedModel):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_EXCLUDED = "excluded"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_EXCLUDED, "Excluded"),
    )

    supplier_product = models.ForeignKey(
        "prices.SupplierProduct",
        on_delete=models.CASCADE,
        related_name="assistant_link_suggestions",
        db_index=True,
    )
    match_group = models.ForeignKey(
        MatchGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="link_suggestions",
        db_index=True,
    )
    suggested_perfume = models.ForeignKey(
        "catalog.Perfume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )
    suggested_variant = models.ForeignKey(
        "catalog.PerfumeVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )
    research_job = models.ForeignKey(
        "assistant_core.ResearchJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )
    confidence = models.PositiveSmallIntegerField(default=0, db_index=True)
    reasoning = models.TextField(blank=True)
    rules_used_json = models.JSONField(default=list, blank=True)
    uncertainties_json = models.JSONField(default=list, blank=True)
    source_engine = models.CharField(max_length=60, default="mock", db_index=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-confidence", "-created_at")
        indexes = [
            models.Index(
                fields=["supplier_product", "status"],
                name="alink_sugg_product_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.supplier_product} / {self.confidence}"


class AIRecommendation(TimeStampedModel):
    TASK_FRAGRANTICA_LINK_RERANK = "fragrantica_link_rerank"
    TASK_NORMALIZATION_REVIEW = "normalization_review"
    TASK_KB_SUGGESTION = "kb_suggestion"
    TASK_CHOICES = (
        (TASK_FRAGRANTICA_LINK_RERANK, "Fragrantica link rerank"),
        (TASK_NORMALIZATION_REVIEW, "Normalization review"),
        (TASK_KB_SUGGESTION, "Knowledge/rule suggestion"),
    )

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_SUPERSEDED = "superseded"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_SUPERSEDED, "Superseded"),
    )

    RISK_UNKNOWN = "unknown"
    RISK_LOW = "low"
    RISK_MEDIUM = "medium"
    RISK_HIGH = "high"
    RISK_CHOICES = (
        (RISK_UNKNOWN, "Unknown"),
        (RISK_LOW, "Low"),
        (RISK_MEDIUM, "Medium"),
        (RISK_HIGH, "High"),
    )

    task_type = models.CharField(max_length=50, choices=TASK_CHOICES, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    supplier_product = models.ForeignKey(
        "prices.SupplierProduct",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_recommendations",
        db_index=True,
    )
    parsed_product = models.ForeignKey(
        ParsedSupplierProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_recommendations",
        db_index=True,
    )
    fragrantica_product = models.ForeignKey(
        FragranticaProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_recommendations",
        db_index=True,
    )
    perfume = models.ForeignKey(
        "catalog.Perfume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assistant_ai_recommendations",
        db_index=True,
    )
    input_hash = models.CharField(max_length=64, db_index=True)
    prompt_version = models.CharField(max_length=40, db_index=True)
    model_name = models.CharField(max_length=80, blank=True)
    confidence = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    risk_level = models.CharField(
        max_length=20,
        choices=RISK_CHOICES,
        default=RISK_UNKNOWN,
        db_index=True,
    )
    input_context_json = models.JSONField(default=dict, blank=True)
    recommendation_json = models.JSONField(default=dict, blank=True)
    reasoning = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(
                fields=["task_type", "status"], name="alink_ai_task_status_idx"
            ),
            models.Index(
                fields=["input_hash", "task_type"], name="alink_ai_input_task_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.task_type} / {self.status} / {self.input_hash[:10]}"


class AILearningProposal(TimeStampedModel):
    PROPOSAL_FRAGRANTICA_LINK_REVIEW = "fragrantica_link_review"
    PROPOSAL_PRODUCT_ALIAS = "product_alias"
    PROPOSAL_BRAND_ALIAS = "brand_alias"
    PROPOSAL_GLOBAL_RULE = "global_rule"
    PROPOSAL_CHOICES = (
        (PROPOSAL_FRAGRANTICA_LINK_REVIEW, "Fragrantica link review"),
        (PROPOSAL_PRODUCT_ALIAS, "Product alias"),
        (PROPOSAL_BRAND_ALIAS, "Brand alias"),
        (PROPOSAL_GLOBAL_RULE, "Global rule"),
    )

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_APPLIED = "applied"
    STATUS_REVERTED = "reverted"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_REVERTED, "Reverted"),
    )

    source_recommendation = models.OneToOneField(
        AIRecommendation,
        on_delete=models.CASCADE,
        related_name="learning_proposal",
        db_index=True,
    )
    proposal_type = models.CharField(
        max_length=40, choices=PROPOSAL_CHOICES, db_index=True
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    title = models.CharField(max_length=220)
    summary = models.TextField(blank=True)
    proposed_action_json = models.JSONField(default=dict, blank=True)
    evidence_json = models.JSONField(default=dict, blank=True)
    impact_json = models.JSONField(default=dict, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(
                fields=["proposal_type", "status"], name="alink_ai_prop_type_status"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.proposal_type} / {self.status} / {self.title}"
