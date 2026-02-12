from django.db import models
from django.utils import timezone


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
    notes = models.TextField(blank=True)
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
    message_id = models.CharField(max_length=255, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=ImportStatus.choices, default=ImportStatus.PENDING
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.supplier} / {self.created_at:%Y-%m-%d %H:%M}"


class ImportFile(models.Model):
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE)
    mapping = models.ForeignKey(
        SupplierFileMapping, on_delete=models.SET_NULL, null=True, blank=True
    )
    file_kind = models.CharField(max_length=10, choices=FileKind.choices)
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="imports/", null=True, blank=True)
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
    recorded_at = models.DateTimeField(default=timezone.now)

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

    def __str__(self) -> str:
        return f"{self.supplier} {self.started_at:%Y-%m-%d %H:%M}"


class ImportSettings(models.Model):
    enabled = models.BooleanField(default=True)
    interval_minutes = models.PositiveIntegerField(default=120)
    last_run_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return "Import settings"

    @classmethod
    def get_solo(cls) -> "ImportSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
