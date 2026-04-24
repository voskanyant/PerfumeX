from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def unique_slug(instance, source: str) -> str:
    base = slugify(source or "") or "item"
    slug = base
    model = instance.__class__
    counter = 2
    while model.objects.filter(slug=slug).exclude(pk=instance.pk).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


class Brand(TimeStampedModel):
    name = models.CharField(max_length=200, unique=True, db_index=True)
    slug = models.SlugField(max_length=220, unique=True, db_index=True, blank=True)
    country_of_origin = models.CharField(max_length=120, blank=True)
    official_url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name",)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Perfume(TimeStampedModel):
    VERIFICATION_DRAFT = "draft"
    VERIFICATION_REVIEW = "review"
    VERIFICATION_VERIFIED = "verified"
    VERIFICATION_CONFLICT = "conflict"
    VERIFICATION_CHOICES = (
        (VERIFICATION_DRAFT, "Draft"),
        (VERIFICATION_REVIEW, "Needs review"),
        (VERIFICATION_VERIFIED, "Verified"),
        (VERIFICATION_CONFLICT, "Conflict"),
    )

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="perfumes")
    name = models.CharField(max_length=220, db_index=True)
    slug = models.SlugField(max_length=260, unique=True, db_index=True, blank=True)
    concentration = models.CharField(max_length=80, blank=True, db_index=True)
    audience = models.CharField(max_length=80, blank=True, db_index=True)
    collection_name = models.CharField(max_length=180, blank=True, db_index=True)
    release_year = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    perfumer_name = models.CharField(max_length=220, blank=True, db_index=True)
    country_of_manufacture = models.CharField(max_length=120, blank=True)
    verification_status = models.CharField(
        max_length=24, choices=VERIFICATION_CHOICES, default=VERIFICATION_REVIEW, db_index=True
    )
    is_published = models.BooleanField(default=False, db_index=True)
    summary_short = models.TextField(blank=True)
    summary_long = models.TextField(blank=True)

    class Meta:
        ordering = ("brand__name", "name")
        indexes = [
            models.Index(fields=["brand", "name"]),
            models.Index(fields=["brand", "concentration", "audience"]),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, f"{self.brand.name} {self.name}")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.brand} / {self.name}"


class PerfumeVariant(TimeStampedModel):
    perfume = models.ForeignKey(Perfume, on_delete=models.CASCADE, related_name="variants")
    size_ml = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, db_index=True)
    size_label = models.CharField(max_length=80, blank=True)
    packaging = models.CharField(max_length=80, blank=True, db_index=True)
    variant_type = models.CharField(max_length=80, blank=True, db_index=True)
    is_tester = models.BooleanField(default=False, db_index=True)
    ean = models.CharField(max_length=64, blank=True, db_index=True)
    sku = models.CharField(max_length=120, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("perfume__brand__name", "perfume__name", "size_ml")
        constraints = [
            models.UniqueConstraint(
                fields=["perfume", "size_ml", "packaging", "variant_type", "is_tester"],
                name="uniq_catalog_perfume_variant_identity",
            )
        ]

    def __str__(self) -> str:
        parts = [str(self.perfume), self.size_label or (f"{self.size_ml:g} ml" if self.size_ml else ""), self.packaging, self.variant_type]
        if self.is_tester:
            parts.append("tester")
        return " / ".join([part for part in parts if part])


class Note(models.Model):
    name = models.CharField(max_length=160, unique=True, db_index=True)
    slug = models.SlugField(max_length=180, unique=True, db_index=True, blank=True)
    family = models.CharField(max_length=120, blank=True, db_index=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("name",)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Accord(models.Model):
    name = models.CharField(max_length=160, unique=True, db_index=True)
    slug = models.SlugField(max_length=180, unique=True, db_index=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("name",)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Source(models.Model):
    SOURCE_OFFICIAL = "official_brand"
    SOURCE_RETAILER = "retailer"
    SOURCE_COMMUNITY = "community"
    SOURCE_OTHER = "other"
    RELIABILITY_HIGH = "high"
    RELIABILITY_MEDIUM = "medium"
    RELIABILITY_LOW = "low"

    perfume = models.ForeignKey(Perfume, on_delete=models.CASCADE, related_name="sources", null=True, blank=True)
    url = models.URLField(db_index=True)
    title = models.CharField(max_length=255, blank=True)
    source_type = models.CharField(max_length=40, default=SOURCE_OTHER, db_index=True)
    source_domain = models.CharField(max_length=160, blank=True, db_index=True)
    priority_rank = models.PositiveSmallIntegerField(default=50)
    reliability = models.CharField(max_length=20, default=RELIABILITY_MEDIUM, db_index=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_current = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("priority_rank", "url")
        constraints = [
            models.UniqueConstraint(fields=["perfume", "url"], name="uniq_catalog_source_perfume_url")
        ]

    def __str__(self) -> str:
        return self.title or self.url


class PerfumeNote(models.Model):
    POSITION_CHOICES = (("top", "Top"), ("middle", "Middle"), ("base", "Base"), ("general", "General"))
    perfume = models.ForeignKey(Perfume, on_delete=models.CASCADE, related_name="perfume_notes", db_index=True)
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="perfume_notes", db_index=True)
    position = models.CharField(max_length=20, choices=POSITION_CHOICES, default="general", db_index=True)
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True)
    confidence = models.CharField(max_length=20, default="medium", db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["perfume", "note", "position", "source"], name="uniq_catalog_perfume_note")
        ]

    def __str__(self) -> str:
        return f"{self.perfume} / {self.position} / {self.note}"


class PerfumeAccord(models.Model):
    perfume = models.ForeignKey(Perfume, on_delete=models.CASCADE, related_name="perfume_accords", db_index=True)
    accord = models.ForeignKey(Accord, on_delete=models.CASCADE, related_name="perfume_accords", db_index=True)
    strength = models.PositiveSmallIntegerField(default=50)
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["perfume", "accord", "source"], name="uniq_catalog_perfume_accord")
        ]

    def __str__(self) -> str:
        return f"{self.perfume} / {self.accord}"


class FactClaim(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CONFLICT = "conflict"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CONFLICT, "Conflict"),
    )

    perfume = models.ForeignKey(Perfume, on_delete=models.CASCADE, related_name="fact_claims", db_index=True)
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="fact_claims", db_index=True)
    field_name = models.CharField(max_length=64, db_index=True)
    value_json = models.JSONField(default=dict)
    confidence = models.CharField(max_length=20, default="medium", db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    claim_hash = models.CharField(max_length=64, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["perfume", "source", "field_name", "claim_hash"],
                name="uniq_catalog_fact_claim",
            )
        ]

    def __str__(self) -> str:
        return f"{self.perfume} / {self.field_name} / {self.status}"


class AIDraft(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_NEEDS_EDITS = "needs_edits"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_NEEDS_EDITS, "Needs edits"),
        (STATUS_REJECTED, "Rejected"),
    )

    perfume = models.ForeignKey(Perfume, on_delete=models.CASCADE, related_name="ai_drafts", db_index=True)
    draft_type = models.CharField(max_length=40, default="description", db_index=True)
    source_claims_json = models.JSONField(default=list)
    content_json = models.JSONField(default=dict)
    model_name = models.CharField(max_length=120, blank=True)
    prompt_version = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    warnings = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.perfume} / {self.draft_type} / {self.status}"
