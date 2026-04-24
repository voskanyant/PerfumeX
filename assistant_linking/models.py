from django.conf import settings
from django.db import models


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


class ProductAlias(TimeStampedModel):
    perfume = models.ForeignKey("catalog.Perfume", on_delete=models.SET_NULL, null=True, blank=True, related_name="product_aliases", db_index=True)
    brand = models.ForeignKey("catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, related_name="product_aliases", db_index=True)
    alias_text = models.CharField(max_length=255, db_index=True)
    canonical_text = models.CharField(max_length=255, db_index=True)
    supplier = models.ForeignKey("prices.Supplier", on_delete=models.CASCADE, null=True, blank=True, related_name="product_aliases", db_index=True)
    concentration = models.CharField(max_length=80, blank=True)
    audience = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=100, db_index=True)

    class Meta:
        ordering = ("supplier__name", "priority", "alias_text")

    def __str__(self) -> str:
        return f"{self.alias_text} -> {self.canonical_text}"


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

    def __str__(self) -> str:
        return f"{self.supplier_product} / {self.confidence}"
