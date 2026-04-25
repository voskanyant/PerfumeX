import os
import re

from django.db import models
from django.utils import timezone
from django.conf import settings
from django.utils.text import slugify


class Currency(models.TextChoices):
    RUB = "RUB", "RUB"
    USD = "USD", "USD"


class ImportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class FileKind(models.TextChoices):
    PRICE = "price", "Price"
    STOCK = "stock", "Stock"


class MappingMode(models.TextChoices):
    NAME = "name", "Column name"
    INDEX = "index", "Column index"


class Supplier(models.Model):
    name = models.CharField(max_length=200, unique=True)
    code = models.CharField(max_length=50, unique=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    default_currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.USD
    )
    from_address_pattern = models.CharField(max_length=200, blank=True)
    price_subject_pattern = models.CharField(max_length=200, blank=True)
    price_filename_pattern = models.CharField(max_length=200, blank=True)
    email_search_days = models.PositiveIntegerField(default=7)
    expected_import_interval_hours = models.PositiveIntegerField(default=24)
    notes = models.TextField(blank=True)
    last_email_check_at = models.DateTimeField(null=True, blank=True)
    last_email_matched = models.PositiveIntegerField(default=0)
    last_email_processed = models.PositiveIntegerField(default=0)
    last_email_errors = models.PositiveIntegerField(default=0)
    last_email_last_message = models.TextField(blank=True)
    last_email_mailboxes = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Mailbox(models.Model):
    IMAP = "imap"
    POP3 = "pop3"
    protocol = models.CharField(
        max_length=10,
        choices=((IMAP, "IMAP"), (POP3, "POP3")),
        default=IMAP,
    )
    name = models.CharField(max_length=100, unique=True)
    host = models.CharField(max_length=200)
    port = models.PositiveIntegerField(default=993)
    username = models.CharField(max_length=200)
    password = models.CharField(max_length=200)
    use_ssl = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100, db_index=True)
    last_inbox_uid = models.BigIntegerField(default=0)
    last_all_mail_uid = models.BigIntegerField(default=0)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # If mailbox was deactivated and is turned on again, reset UID cursors
        # so importer doesn't stay pinned to stale offsets.
        if self.pk:
            previous = Mailbox.objects.filter(pk=self.pk).values(
                "is_active", "last_inbox_uid", "last_all_mail_uid", "last_checked_at"
            ).first()
            if previous and not previous["is_active"] and self.is_active:
                self.last_inbox_uid = 0
                self.last_all_mail_uid = 0
                self.last_checked_at = None
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class SupplierMailboxRule(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    mailbox = models.ForeignKey(Mailbox, on_delete=models.CASCADE)
    from_pattern = models.CharField(max_length=200, blank=True)
    subject_pattern = models.CharField(max_length=200, blank=True)
    filename_pattern = models.CharField(max_length=200, blank=True)
    match_price_files = models.BooleanField(default=True)
    match_stock_files = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.supplier} / {self.mailbox}"


class SupplierFileMapping(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    file_kind = models.CharField(max_length=10, choices=FileKind.choices)
    mapping_mode = models.CharField(
        max_length=10, choices=MappingMode.choices, default=MappingMode.NAME
    )
    sheet_names = models.TextField(blank=True)
    sheet_indexes = models.CharField(max_length=200, blank=True)
    sheet_name = models.CharField(max_length=200, blank=True)
    sheet_index = models.PositiveIntegerField(null=True, blank=True)
    header_row = models.PositiveIntegerField(default=1)
    column_map = models.JSONField(default=dict)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.supplier} / {self.file_kind}"


class SupplierProduct(models.Model):
    our_product = models.ForeignKey(
        "OurProduct", on_delete=models.SET_NULL, null=True, blank=True
    )
    catalog_perfume = models.ForeignKey(
        "catalog.Perfume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_products",
        db_index=True,
    )
    catalog_variant = models.ForeignKey(
        "catalog.PerfumeVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_products",
        db_index=True,
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    supplier_sku = models.CharField(max_length=200, blank=True)
    identity_key = models.CharField(max_length=255, db_index=True, blank=True)
    name = models.CharField(max_length=300)
    brand = models.CharField(max_length=200, blank=True)
    size = models.CharField(max_length=100, blank=True)
    currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.RUB
    )
    current_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    current_stock = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    last_imported_at = models.DateTimeField(null=True, blank=True)
    last_import_batch = models.ForeignKey(
        "ImportBatch", on_delete=models.SET_NULL, null=True, blank=True
    )
    created_import_batch = models.ForeignKey(
        "ImportBatch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_products",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "identity_key"], name="uniq_supplier_identity_key"
            )
        ]
        indexes = [
            models.Index(fields=["name"], name="prices_sp_name_idx"),
            models.Index(fields=["supplier_sku"], name="prices_sp_sku_idx"),
            models.Index(
                fields=["is_active", "supplier"],
                name="prices_sp_active_supplier_idx",
            ),
            models.Index(
                fields=["is_active", "current_price"],
                name="prices_sp_active_price_idx",
            ),
            models.Index(
                fields=["is_active", "last_imported_at"],
                name="prices_sp_active_lastimp_idx",
            ),
        ]

    def __str__(self) -> str:
        label = self.supplier_sku or self.name
        return f"{self.supplier} / {label}"


class OurProduct(models.Model):
    name = models.CharField(max_length=300)
    brand = models.CharField(max_length=200, blank=True)
    size = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("name", "brand", "size")

    def __str__(self) -> str:
        parts = [self.brand, self.name, self.size]
        return " ".join([part for part in parts if part])


class ImportBatch(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    mailbox = models.ForeignKey(
        Mailbox, on_delete=models.SET_NULL, null=True, blank=True
    )
    message_folder = models.CharField(max_length=255, blank=True)
    message_id = models.CharField(max_length=255, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=ImportStatus.choices, default=ImportStatus.PENDING
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.supplier} / {self.created_at:%Y-%m-%d %H:%M}"


def _safe_file_part(value: str) -> str:
    text = (value or "").strip().replace(" ", "_")
    text = re.sub(r"[^\w\-.]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text or "file"


def _strip_leading_datetime_prefix(value: str) -> str:
    """
    Remove legacy leading datetime prefixes to avoid building names like:
    2026-02-24_20-00_supplier_2026-02-24_20-00_original.xlsx
    """
    text = (value or "").strip()
    if not text:
        return text

    # Examples handled:
    # 2026-02-24_20-00_file
    # 2026-02-24_20-00-15_file
    # supplier_2026-02-24_20-00_file
    # supplier__2026-02-24_20-00__file
    datetime_token = r"\d{4}-\d{2}-\d{2}[ _-]\d{2}[-_:]\d{2}(?:[-_:]\d{2})?"
    patterns = [
        rf"^(?:{datetime_token})[_-]*",
        rf"^[A-Za-z0-9._-]{{2,48}}__?(?:{datetime_token})[_-]*",
    ]
    for _ in range(3):
        updated = text
        for pattern in patterns:
            updated = re.sub(pattern, "", updated, count=1)
        updated = updated.strip("._-")
        if updated == text:
            break
        text = updated
    return text or value


def build_import_file_path(import_file: "ImportFile", filename: str) -> str:
    # Keep full storage path <= 100 chars for deployments where FileField DB column
    # is still varchar(100).
    max_path_len = 100
    raw_name = os.path.basename((filename or "").strip()) or "file"
    stem, ext = os.path.splitext(raw_name)
    safe_ext = ext[:12] if ext else ""

    supplier = getattr(getattr(import_file, "import_batch", None), "supplier", None)
    supplier_id = getattr(supplier, "id", None) or 0
    supplier_name = getattr(supplier, "name", "") or ""
    supplier_slug_full = slugify(supplier_name) or "supplier"
    supplier_slug_folder = supplier_slug_full[:24] or "supplier"
    supplier_slug_file = supplier_slug_full[:16] or "supplier"
    supplier_folder = f"{supplier_id:04d}_{supplier_slug_folder}"

    batch = getattr(import_file, "import_batch", None)
    received_at = getattr(batch, "received_at", None) or getattr(batch, "created_at", None)
    if received_at is None:
        received_at = timezone.now()
    local_received = timezone.localtime(received_at)
    dt_prefix = local_received.strftime("%Y-%m-%d_%H-%M")

    normalized_stem = _strip_leading_datetime_prefix(stem)
    original_part = _safe_file_part(normalized_stem)
    base_prefix = f"{dt_prefix}_{supplier_slug_file}_"

    folder_prefix = f"imports/{supplier_folder}/"
    reserved = len(folder_prefix) + len(base_prefix) + len(safe_ext)
    available_for_original = max(8, max_path_len - reserved)
    shortened_original = original_part[:available_for_original]
    final_name = f"{base_prefix}{shortened_original}{safe_ext}"
    path = f"{folder_prefix}{final_name}"

    if len(path) > max_path_len:
        fallback_name = f"{dt_prefix}_{supplier_id}_{getattr(import_file, 'id', 0) or 0}{safe_ext}"
        path = f"{folder_prefix}{fallback_name}"
    if len(path) > max_path_len:
        overflow = len(path) - max_path_len
        trimmed_folder = supplier_folder[:-overflow] if overflow < len(supplier_folder) else str(supplier_id)
        path = f"imports/{trimmed_folder}/{dt_prefix}_{supplier_id}{safe_ext}"
    return path[:max_path_len]


def import_file_upload_to(import_file: "ImportFile", filename: str) -> str:
    return build_import_file_path(import_file, filename)


class ImportFile(models.Model):
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE)
    mapping = models.ForeignKey(
        SupplierFileMapping, on_delete=models.SET_NULL, null=True, blank=True
    )
    file_kind = models.CharField(max_length=10, choices=FileKind.choices)
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=import_file_upload_to, null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=20, choices=ImportStatus.choices, default=ImportStatus.PENDING
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.import_batch} / {self.filename}"


class PriceSnapshot(models.Model):
    supplier_product = models.ForeignKey(SupplierProduct, on_delete=models.CASCADE)
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.SET_NULL, null=True, blank=True
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, choices=Currency.choices)
    price_rub = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    price_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["supplier_product", "-recorded_at"]),
            models.Index(fields=["supplier_product", "recorded_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.supplier_product} / {self.price} {self.currency}"


class StockSnapshot(models.Model):
    supplier_product = models.ForeignKey(SupplierProduct, on_delete=models.CASCADE)
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.SET_NULL, null=True, blank=True
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.supplier_product} / {self.quantity}"


class ExchangeRate(models.Model):
    rate_date = models.DateField()
    from_currency = models.CharField(max_length=3, choices=Currency.choices)
    to_currency = models.CharField(max_length=3, choices=Currency.choices)
    rate = models.DecimalField(max_digits=12, decimal_places=6)
    source = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("rate_date", "from_currency", "to_currency")

    def __str__(self) -> str:
        return f"{self.rate_date} {self.from_currency}/{self.to_currency}"


class EmailImportStatus(models.TextChoices):
    RUNNING = "running", "Running"
    FINISHED = "finished", "Finished"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class EmailImportRun(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    status = models.CharField(
        max_length=20, choices=EmailImportStatus.choices, default=EmailImportStatus.RUNNING
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    total_messages = models.PositiveIntegerField(default=0)
    processed_messages = models.PositiveIntegerField(default=0)
    matched_files = models.PositiveIntegerField(default=0)
    processed_files = models.PositiveIntegerField(default=0)
    skipped_duplicates = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    last_message = models.TextField(blank=True)
    detailed_log = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.supplier} {self.started_at:%Y-%m-%d %H:%M}"


class ImportSettings(models.Model):
    enabled = models.BooleanField(default=True)
    interval_minutes = models.PositiveIntegerField(default=120)
    auto_mark_seen = models.BooleanField(default=True)
    max_messages_per_run = models.PositiveIntegerField(default=200)
    supplier_batch_size = models.PositiveIntegerField(default=10)
    supplier_batch_offset = models.PositiveIntegerField(default=0)
    supplier_timeout_minutes = models.PositiveIntegerField(default=5)
    deactivate_products_after_days = models.PositiveIntegerField(default=0)
    cbr_markup_percent = models.DecimalField(max_digits=6, decimal_places=3, default=3.0)
    filename_blacklist_terms = models.TextField(
        blank=True,
        default="сверка\nнакладная\ninvoice\nакт\nreport\nнакл",
    )
    last_run_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return "Import settings"

    @classmethod
    def get_solo(cls) -> "ImportSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def get_filename_blacklist(self) -> list[str]:
        raw = (self.filename_blacklist_terms or "").replace(";", "\n").replace(",", "\n")
        terms = [term.strip().lower() for term in raw.splitlines() if term.strip()]
        return terms


class UserPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="price_prefs"
    )
    supplier_exclude_terms = models.TextField(blank=True, default="")
    supplier_front_filters = models.JSONField(blank=True, default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Preferences: {self.user}"

    @classmethod
    def get_for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj
