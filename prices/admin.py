from django.contrib import admin

from . import models


@admin.register(models.Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "default_currency", "is_active", "created_at")
    search_fields = ("name", "code")
    list_filter = ("is_active", "default_currency")


@admin.register(models.Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    list_display = ("name", "protocol", "host", "username", "is_active")
    search_fields = ("name", "host", "username")
    list_filter = ("protocol", "is_active")


@admin.register(models.SupplierMailboxRule)
class SupplierMailboxRuleAdmin(admin.ModelAdmin):
    list_display = (
        "supplier",
        "mailbox",
        "match_price_files",
        "match_stock_files",
        "is_active",
    )
    search_fields = ("supplier__name", "mailbox__name")
    list_filter = ("is_active", "match_price_files", "match_stock_files")


@admin.register(models.SupplierFileMapping)
class SupplierFileMappingAdmin(admin.ModelAdmin):
    list_display = ("supplier", "file_kind", "mapping_mode", "is_active")
    search_fields = ("supplier__name",)
    list_filter = ("file_kind", "mapping_mode", "is_active")


@admin.register(models.SupplierProduct)
class SupplierProductAdmin(admin.ModelAdmin):
    list_display = (
        "supplier",
        "supplier_sku",
        "name",
        "currency",
        "current_price",
        "current_stock",
        "last_imported_at",
        "is_active",
    )
    search_fields = ("supplier__name", "supplier_sku", "name", "brand")
    list_filter = ("supplier", "currency", "is_active")


@admin.register(models.ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("supplier", "mailbox", "received_at", "status", "created_at")
    search_fields = ("supplier__name", "message_id")
    list_filter = ("status", "supplier")


@admin.register(models.ImportFile)
class ImportFileAdmin(admin.ModelAdmin):
    list_display = (
        "import_batch",
        "mapping",
        "file_kind",
        "filename",
        "status",
        "processed_at",
    )
    search_fields = ("filename",)
    list_filter = ("file_kind", "status")


@admin.register(models.PriceSnapshot)
class PriceSnapshotAdmin(admin.ModelAdmin):
    list_display = ("supplier_product", "price", "currency", "recorded_at")
    search_fields = ("supplier_product__supplier_sku", "supplier_product__name")
    list_filter = ("currency",)


@admin.register(models.StockSnapshot)
class StockSnapshotAdmin(admin.ModelAdmin):
    list_display = ("supplier_product", "quantity", "recorded_at")
    search_fields = ("supplier_product__supplier_sku", "supplier_product__name")


@admin.register(models.ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("rate_date", "from_currency", "to_currency", "rate", "source")
    list_filter = ("from_currency", "to_currency")
