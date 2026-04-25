import re

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models

from assistant_linking.utils.text import normalize_alias_value
from catalog.models import compact_decimal_text


CONCENTRATION_ALIAS_CACHE_KEY = "assistant_linking:concentration_aliases:v1"
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
        lower_allowed = index > 0
        hyphen_parts = []
        for hyphen_part in word.split("-"):
            apostrophe_parts = hyphen_part.split("'")
            hyphen_parts.append(
                "'".join(
                    part.lower()
                    if sub_index > 0 and part.lower() in TITLECASE_APOSTROPHE_SUFFIXES
                    else title_piece(part, lower_allowed=lower_allowed and sub_index == 0)
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
    brand = models.ForeignKey("catalog.Brand", on_delete=models.CASCADE, related_name="aliases", db_index=True)
    alias_text = models.CharField(max_length=255, db_index=True)
    normalized_alias = models.CharField(max_length=255, db_index=True)
    supplier = models.ForeignKey("prices.Supplier", on_delete=models.CASCADE, null=True, blank=True, related_name="brand_aliases", db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=100, db_index=True)
    is_regex = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("supplier__name", "priority", "alias_text")
        constraints = [
            models.UniqueConstraint(fields=["alias_text", "supplier", "brand"], name="uniq_assistant_brand_alias")
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
                raise ValidationError({"normalized_alias": "Pattern too long (max 200 chars)."})
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
    perfume = models.ForeignKey("catalog.Perfume", on_delete=models.SET_NULL, null=True, blank=True, related_name="product_aliases", db_index=True)
    brand = models.ForeignKey("catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, related_name="product_aliases", db_index=True)
    alias_text = models.CharField(max_length=255, db_index=True)
    canonical_text = models.CharField(max_length=255, db_index=True)
    supplier = models.ForeignKey("prices.Supplier", on_delete=models.CASCADE, null=True, blank=True, related_name="product_aliases", db_index=True)
    concentration = models.CharField(max_length=80, blank=True)
    audience = models.CharField(max_length=80, blank=True)
    excluded_terms = models.TextField(blank=True)
    active = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=100, db_index=True)

    class Meta:
        ordering = ("supplier__name", "priority", "alias_text")

    def __str__(self) -> str:
        return f"{self.alias_text} -> {self.canonical_text}"


class ConcentrationAlias(TimeStampedModel):
    concentration = models.CharField(max_length=80, db_index=True)
    alias_text = models.CharField(max_length=255, db_index=True)
    normalized_alias = models.CharField(max_length=255, db_index=True)
    supplier = models.ForeignKey("prices.Supplier", on_delete=models.CASCADE, null=True, blank=True, related_name="concentration_aliases", db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=100, db_index=True)
    is_regex = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("supplier__name", "priority", "alias_text")
        constraints = [
            models.UniqueConstraint(fields=["alias_text", "supplier", "concentration"], name="uniq_assistant_concentration_alias")
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
    supplier_product = models.OneToOneField("prices.SupplierProduct", on_delete=models.CASCADE, related_name="assistant_parse")
    raw_name = models.TextField()
    normalized_text = models.TextField(db_index=True)
    detected_brand_text = models.CharField(max_length=255, blank=True)
    normalized_brand = models.ForeignKey("catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    product_name_text = models.CharField(max_length=255, blank=True, db_index=True)
    concentration = models.CharField(max_length=80, blank=True, db_index=True)
    size_ml = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, db_index=True)
    raw_size_text = models.CharField(max_length=80, blank=True)
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
    parser_version = models.CharField(max_length=40, default="deterministic-v1")
    locked_by_human = models.BooleanField(default=False, db_index=True)
    last_parsed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("supplier_product__supplier__name", "supplier_product__name")

    def __str__(self) -> str:
        return f"Parsed: {self.supplier_product}"

    @property
    def display_brand(self) -> str:
        if self.normalized_brand_id:
            return str(self.normalized_brand)
        return self.detected_brand_text

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
    def display_variant_type(self) -> str:
        if self.is_tester or self.variant_type == "tester":
            return "Tester"
        if self.is_sample or self.variant_type == "sample":
            return "Sample"
        if self.is_travel or self.variant_type == "travel":
            return "Travel"
        if self.is_set or self.variant_type == "set":
            return "Set"
        return display_label(self.variant_type, default="Standard")

    @property
    def product_category_label(self) -> str:
        if self.concentration in HAIR_CARE_CATEGORY_CONCENTRATIONS:
            return "Hair Care"
        if self.concentration in PERFUME_CATEGORY_CONCENTRATIONS:
            return "Perfume"
        return "Unknown"

    @property
    def display_packaging(self) -> str:
        return display_label(self.packaging, default="Standard")

    @property
    def identity_variant_label(self) -> str:
        variant = self.display_variant_type
        if variant and variant != "Standard":
            return variant
        return ""

    @property
    def identity_packaging_label(self) -> str:
        packaging = self.display_packaging
        if packaging and packaging != "Standard" and packaging != self.identity_variant_label:
            return packaging
        return ""

    @property
    def display_identity(self) -> str:
        parts = [
            self.display_brand,
            self.display_product_name,
            self.concentration,
            self.display_size,
            self.identity_variant_label,
            self.identity_packaging_label,
        ]
        return " / ".join(part for part in parts if part)


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
    normalized_brand = models.ForeignKey("catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    canonical_name = models.CharField(max_length=255, db_index=True)
    concentration = models.CharField(max_length=80, blank=True, db_index=True)
    audience_hint = models.CharField(max_length=80, blank=True, db_index=True)
    size_ml = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, db_index=True)
    packaging = models.CharField(max_length=80, blank=True, db_index=True)
    variant_type = models.CharField(max_length=80, blank=True, db_index=True)
    candidate_perfume = models.ForeignKey("catalog.Perfume", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    candidate_variant = models.ForeignKey("catalog.PerfumeVariant", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
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

    match_group = models.ForeignKey(MatchGroup, on_delete=models.CASCADE, related_name="items", db_index=True)
    supplier_product = models.ForeignKey("prices.SupplierProduct", on_delete=models.CASCADE, related_name="assistant_group_items", db_index=True)
    parsed_product = models.ForeignKey(ParsedSupplierProduct, on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER, db_index=True)
    match_score = models.PositiveSmallIntegerField(default=50, db_index=True)
    reasoning = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["match_group", "supplier_product"], name="uniq_assistant_group_item")
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

    supplier_product = models.ForeignKey("prices.SupplierProduct", on_delete=models.CASCADE, related_name="assistant_decisions", db_index=True)
    perfume = models.ForeignKey("catalog.Perfume", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    variant = models.ForeignKey("catalog.PerfumeVariant", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    decision_type = models.CharField(max_length=40, choices=DECISION_CHOICES, db_index=True)
    reason = models.TextField(blank=True)
    apply_to_similar = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
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

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_link_actions")
    action_type = models.CharField(max_length=40, choices=ACTION_CHOICES, db_index=True)
    payload_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["user", "-created_at"], name="alink_action_user_created_idx"),
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

    supplier_product = models.ForeignKey("prices.SupplierProduct", on_delete=models.CASCADE, related_name="assistant_link_suggestions", db_index=True)
    match_group = models.ForeignKey(MatchGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="link_suggestions", db_index=True)
    suggested_perfume = models.ForeignKey("catalog.Perfume", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    suggested_variant = models.ForeignKey("catalog.PerfumeVariant", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    research_job = models.ForeignKey("assistant_core.ResearchJob", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    confidence = models.PositiveSmallIntegerField(default=0, db_index=True)
    reasoning = models.TextField(blank=True)
    rules_used_json = models.JSONField(default=list, blank=True)
    uncertainties_json = models.JSONField(default=list, blank=True)
    source_engine = models.CharField(max_length=60, default="mock", db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
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
