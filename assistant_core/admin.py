from django.contrib import admin

from . import models


@admin.register(models.GlobalRule)
class GlobalRuleAdmin(admin.ModelAdmin):
    list_display = ("title", "rule_kind", "scope_type", "priority", "confidence", "active", "approved")
    search_fields = ("title", "rule_text", "scope_value")
    list_filter = ("rule_kind", "scope_type", "active", "approved")


@admin.register(models.SupplierRule)
class SupplierRuleAdmin(admin.ModelAdmin):
    list_display = ("supplier", "brand", "title", "rule_kind", "priority", "confidence", "active", "approved")
    search_fields = ("supplier__name", "brand__name", "title", "rule_text")
    list_filter = ("rule_kind", "active", "approved", "supplier")


@admin.register(models.KnowledgeNote)
class KnowledgeNoteAdmin(admin.ModelAdmin):
    list_display = ("category", "title", "supplier", "brand", "perfume", "active")
    search_fields = ("category", "title", "content", "supplier__name", "brand__name", "perfume__name")
    list_filter = ("category", "active")


@admin.register(models.ResearchJob)
class ResearchJobAdmin(admin.ModelAdmin):
    list_display = ("job_type", "status", "brand", "perfume", "created_at", "finished_at")
    search_fields = ("job_type", "query", "result_summary", "brand__name", "perfume__name")
    list_filter = ("job_type", "status")


@admin.register(models.BrandWatchProfile)
class BrandWatchProfileAdmin(admin.ModelAdmin):
    list_display = ("brand", "watch_frequency", "active", "last_checked_at", "last_success_at")
    search_fields = ("brand__name", "official_url", "instructions")
    list_filter = ("watch_frequency", "active")


@admin.register(models.SourceSnapshot)
class SourceSnapshotAdmin(admin.ModelAdmin):
    list_display = ("brand_profile", "source_type", "fetch_status", "checked_at", "url")
    search_fields = ("brand_profile__brand__name", "url", "title", "extracted_summary")
    list_filter = ("source_type", "fetch_status")


@admin.register(models.DetectedChange)
class DetectedChangeAdmin(admin.ModelAdmin):
    list_display = ("brand_profile", "change_type", "field_name", "confidence", "status", "created_at")
    search_fields = ("brand_profile__brand__name", "change_type", "field_name", "explanation")
    list_filter = ("change_type", "status", "confidence")
