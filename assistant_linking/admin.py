from django.contrib import admin

from . import models


@admin.register(models.BrandAlias)
class BrandAliasAdmin(admin.ModelAdmin):
    list_display = ("alias_text", "brand", "supplier", "priority", "active", "is_regex")
    search_fields = ("alias_text", "normalized_alias", "brand__name", "supplier__name")
    list_filter = ("active", "is_regex", "supplier")


@admin.register(models.ProductAlias)
class ProductAliasAdmin(admin.ModelAdmin):
    list_display = ("alias_text", "canonical_text", "brand", "perfume", "supplier", "priority", "active")
    search_fields = ("alias_text", "canonical_text", "excluded_terms", "brand__name", "perfume__name", "supplier__name")
    list_filter = ("active", "supplier", "brand")


@admin.register(models.ConcentrationAlias)
class ConcentrationAliasAdmin(admin.ModelAdmin):
    list_display = ("alias_text", "concentration", "supplier", "priority", "active", "is_regex")
    search_fields = ("alias_text", "normalized_alias", "concentration", "supplier__name")
    list_filter = ("active", "is_regex", "supplier", "concentration")


@admin.register(models.ParsedSupplierProduct)
class ParsedSupplierProductAdmin(admin.ModelAdmin):
    list_display = ("supplier_product", "normalized_brand", "product_name_text", "concentration", "size_ml", "confidence", "locked_by_human")
    search_fields = ("supplier_product__name", "normalized_text", "product_name_text", "detected_brand_text")
    list_filter = ("concentration", "supplier_gender_hint", "is_tester", "locked_by_human")


@admin.register(models.MatchGroup)
class MatchGroupAdmin(admin.ModelAdmin):
    list_display = ("canonical_name", "normalized_brand", "concentration", "size_ml", "status", "confidence")
    search_fields = ("group_key", "canonical_name", "normalized_brand__name")
    list_filter = ("status", "concentration", "variant_type", "packaging")


@admin.register(models.MatchGroupItem)
class MatchGroupItemAdmin(admin.ModelAdmin):
    list_display = ("match_group", "supplier_product", "role", "match_score")
    search_fields = ("match_group__group_key", "supplier_product__name", "supplier_product__supplier__name")
    list_filter = ("role",)


@admin.register(models.ManualLinkDecision)
class ManualLinkDecisionAdmin(admin.ModelAdmin):
    list_display = ("supplier_product", "perfume", "variant", "decision_type", "apply_to_similar", "created_at")
    search_fields = ("supplier_product__name", "perfume__name", "variant__sku", "reason")
    list_filter = ("decision_type", "apply_to_similar")


@admin.register(models.ManualLinkDecisionAudit)
class ManualLinkDecisionAuditAdmin(admin.ModelAdmin):
    list_display = ("previous_pk", "replaced_by", "replaced_at")
    search_fields = ("previous_pk", "previous_decision_json")
    readonly_fields = ("previous_pk", "previous_decision_json", "replaced_by", "replaced_at")


@admin.register(models.LinkSuggestion)
class LinkSuggestionAdmin(admin.ModelAdmin):
    list_display = ("supplier_product", "suggested_perfume", "suggested_variant", "confidence", "source_engine", "status")
    search_fields = ("supplier_product__name", "suggested_perfume__name", "reasoning")
    list_filter = ("source_engine", "status", "confidence")
