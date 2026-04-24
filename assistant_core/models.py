from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class GlobalRule(TimeStampedModel):
    title = models.CharField(max_length=220, db_index=True)
    rule_kind = models.CharField(max_length=60, db_index=True)
    scope_type = models.CharField(max_length=60, db_index=True)
    scope_value = models.CharField(max_length=220, blank=True, db_index=True)
    rule_text = models.TextField()
    examples_json = models.JSONField(default=list, blank=True)
    priority = models.IntegerField(default=100, db_index=True)
    confidence = models.PositiveSmallIntegerField(default=70, db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    approved = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ("priority", "title")

    def __str__(self) -> str:
        return self.title


class SupplierRule(TimeStampedModel):
    supplier = models.ForeignKey("prices.Supplier", on_delete=models.CASCADE, related_name="assistant_rules", db_index=True)
    brand = models.ForeignKey("catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    title = models.CharField(max_length=220, db_index=True)
    rule_kind = models.CharField(max_length=60, db_index=True)
    applies_to_text = models.CharField(max_length=220, blank=True, db_index=True)
    rule_text = models.TextField()
    examples_json = models.JSONField(default=list, blank=True)
    priority = models.IntegerField(default=100, db_index=True)
    confidence = models.PositiveSmallIntegerField(default=70, db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    approved = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ("supplier__name", "priority", "title")

    def __str__(self) -> str:
        return f"{self.supplier} / {self.title}"


class KnowledgeNote(TimeStampedModel):
    category = models.CharField(max_length=80, db_index=True)
    title = models.CharField(max_length=220, db_index=True)
    content = models.TextField()
    supplier = models.ForeignKey("prices.Supplier", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    brand = models.ForeignKey("catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    perfume = models.ForeignKey("catalog.Perfume", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ("category", "title")

    def __str__(self) -> str:
        return self.title


class ResearchJob(TimeStampedModel):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_FINISHED = "finished"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_FINISHED, "Finished"),
        (STATUS_FAILED, "Failed"),
    )

    job_type = models.CharField(max_length=60, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    query = models.TextField(blank=True)
    context_json = models.JSONField(default=dict, blank=True)
    result_summary = models.TextField(blank=True)
    raw_result_json = models.JSONField(default=dict, blank=True)
    brand = models.ForeignKey("catalog.Brand", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    perfume = models.ForeignKey("catalog.Perfume", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.job_type} / {self.status} / {self.created_at:%Y-%m-%d %H:%M}"


class BrandWatchProfile(TimeStampedModel):
    brand = models.OneToOneField("catalog.Brand", on_delete=models.CASCADE, related_name="watch_profile")
    official_url = models.URLField(blank=True)
    trusted_sources_json = models.JSONField(default=list, blank=True)
    watch_frequency = models.CharField(max_length=40, default="weekly", db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    instructions = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("brand__name",)

    def __str__(self) -> str:
        return f"Watch: {self.brand}"


class SourceSnapshot(models.Model):
    brand_profile = models.ForeignKey(BrandWatchProfile, on_delete=models.CASCADE, related_name="source_snapshots", db_index=True)
    url = models.URLField(db_index=True)
    title = models.CharField(max_length=255, blank=True)
    source_type = models.CharField(max_length=60, db_index=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    extracted_text = models.TextField(blank=True)
    extracted_summary = models.TextField(blank=True)
    raw_facts_json = models.JSONField(default=dict, blank=True)
    checked_at = models.DateTimeField(db_index=True)
    fetch_status = models.CharField(max_length=40, default="ok", db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["brand_profile", "url", "content_hash"],
                name="uniq_assistant_source_snapshot",
            )
        ]
        ordering = ("-checked_at",)

    def __str__(self) -> str:
        return self.title or self.url


class DetectedChange(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_NEEDS_RESEARCH = "needs_research"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_NEEDS_RESEARCH, "Needs more research"),
    )

    brand_profile = models.ForeignKey(BrandWatchProfile, on_delete=models.CASCADE, related_name="detected_changes", db_index=True)
    perfume = models.ForeignKey("catalog.Perfume", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    change_type = models.CharField(max_length=60, db_index=True)
    field_name = models.CharField(max_length=80, blank=True, db_index=True)
    old_value_json = models.JSONField(default=dict, blank=True)
    new_value_json = models.JSONField(default=dict, blank=True)
    explanation = models.TextField(blank=True)
    confidence = models.PositiveSmallIntegerField(default=50, db_index=True)
    source_urls_json = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.brand_profile} / {self.change_type} / {self.status}"
