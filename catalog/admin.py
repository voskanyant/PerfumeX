from django.contrib import admin

from . import models


@admin.register(models.Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "country_of_origin", "is_active", "updated_at")
    search_fields = ("name", "slug", "official_url")
    list_filter = ("is_active", "country_of_origin")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(models.Perfume)
class PerfumeAdmin(admin.ModelAdmin):
    list_display = ("brand", "name", "concentration", "audience", "verification_status", "is_published")
    search_fields = ("brand__name", "name", "collection_name", "perfumer_name")
    list_filter = ("verification_status", "is_published", "concentration", "audience")


@admin.register(models.PerfumeVariant)
class PerfumeVariantAdmin(admin.ModelAdmin):
    list_display = ("perfume", "size_ml", "packaging", "variant_type", "is_tester", "is_active")
    search_fields = ("perfume__name", "perfume__brand__name", "ean", "sku")
    list_filter = ("packaging", "variant_type", "is_tester", "is_active")


@admin.register(models.Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("name", "family")
    search_fields = ("name", "family")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(models.PerfumeNote)
class PerfumeNoteAdmin(admin.ModelAdmin):
    list_display = ("perfume", "position", "note", "confidence", "source")
    search_fields = ("perfume__name", "perfume__brand__name", "note__name")
    list_filter = ("position", "confidence")


@admin.register(models.Accord)
class AccordAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(models.PerfumeAccord)
class PerfumeAccordAdmin(admin.ModelAdmin):
    list_display = ("perfume", "accord", "strength", "source")
    search_fields = ("perfume__name", "perfume__brand__name", "accord__name")


@admin.register(models.Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("perfume", "source_type", "source_domain", "reliability", "is_current")
    search_fields = ("perfume__name", "perfume__brand__name", "url", "title", "source_domain")
    list_filter = ("source_type", "reliability", "is_current")


@admin.register(models.FactClaim)
class FactClaimAdmin(admin.ModelAdmin):
    list_display = ("perfume", "field_name", "confidence", "status", "created_at")
    search_fields = ("perfume__name", "perfume__brand__name", "field_name", "claim_hash")
    list_filter = ("status", "confidence", "field_name")


@admin.register(models.AIDraft)
class AIDraftAdmin(admin.ModelAdmin):
    list_display = ("perfume", "draft_type", "model_name", "status", "created_at")
    search_fields = ("perfume__name", "perfume__brand__name", "draft_type", "model_name")
    list_filter = ("status", "draft_type", "model_name")
