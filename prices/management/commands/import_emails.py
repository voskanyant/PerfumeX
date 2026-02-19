from django.core.management.base import BaseCommand
from django.utils import timezone

from prices import models
from prices.services.email_importer import run_import
from prices.services.cbr_rates import upsert_cbr_markup_rates
from prices.services.importer import process_import_file


def _get_supplier_latest_batch_time(supplier: models.Supplier):
    latest = None
    batches = models.ImportBatch.objects.filter(
        supplier=supplier,
        importfile__status=models.ImportStatus.PROCESSED,
        importfile__file_kind=models.FileKind.PRICE,
    ).values_list("received_at", "created_at")
    for received_at, created_at in batches:
        candidate = received_at or created_at
        if candidate and (latest is None or candidate > latest):
            latest = candidate
    return latest


class Command(BaseCommand):
    help = "Import supplier price lists from email attachments."

    def add_arguments(self, parser):
        parser.add_argument("--mailbox", help="Mailbox name to process.")
        parser.add_argument("--all", action="store_true", help="Process all messages.")
        parser.add_argument("--limit", type=int, default=0, help="Limit messages.")
        parser.add_argument(
            "--mark-seen",
            action="store_true",
            help="Mark processed messages as seen.",
        )
        parser.add_argument(
            "--max-bytes",
            type=int,
            default=20_000_000,
            help="Skip emails larger than this size (bytes).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run immediately, ignoring Import Settings interval/disabled flag.",
        )

    def handle(self, *args, **options):
        # Recover rows left in pending when previous cron run was killed
        # (for example by external timeout) so they can be retried safely.
        stale_cutoff = timezone.now() - timezone.timedelta(minutes=15)
        stale_files_qs = models.ImportFile.objects.filter(
            status=models.ImportStatus.PENDING,
            import_batch__created_at__lt=stale_cutoff,
        )
        stale_file_ids = list(stale_files_qs.values_list("id", flat=True))
        if stale_file_ids:
            stale_files_qs.update(
                status=models.ImportStatus.FAILED,
                error_message="Auto-failed stale pending file. Previous run was likely interrupted.",
            )
            models.ImportBatch.objects.filter(
                id__in=models.ImportFile.objects.filter(id__in=stale_file_ids).values_list(
                    "import_batch_id", flat=True
                ),
                status=models.ImportStatus.PENDING,
            ).update(
                status=models.ImportStatus.FAILED,
                error_message="Auto-failed stale pending batch. Previous run was likely interrupted.",
            )
            self.stdout.write(
                f"Recovered stale pending imports: {len(stale_file_ids)} file(s)."
            )

        # Retry auto-failed stale files directly from saved file payload
        # so we don't depend on IMAP fetching the same message again.
        retry_qs = (
            models.ImportFile.objects.select_related("import_batch", "import_batch__supplier")
            .filter(
                status=models.ImportStatus.FAILED,
                error_message__startswith="Auto-failed stale pending file.",
                file__isnull=False,
            )
            .order_by("id")[:100]
        )
        retried = 0
        for import_file in retry_qs:
            supplier = import_file.import_batch.supplier
            if import_file.content_hash and models.ImportFile.objects.filter(
                import_batch__supplier=supplier,
                content_hash=import_file.content_hash,
                status=models.ImportStatus.PROCESSED,
            ).exists():
                continue
            import_file.status = models.ImportStatus.PENDING
            import_file.error_message = ""
            import_file.save(update_fields=["status", "error_message"])
            try:
                process_import_file(import_file)
                import_file.status = models.ImportStatus.PROCESSED
                import_file.save(update_fields=["status"])
                models.ImportBatch.objects.filter(id=import_file.import_batch_id).update(
                    status=models.ImportStatus.PROCESSED,
                    error_message="",
                )
                retried += 1
            except Exception as exc:
                import_file.status = models.ImportStatus.FAILED
                import_file.error_message = str(exc)
                import_file.save(update_fields=["status", "error_message"])
                models.ImportBatch.objects.filter(id=import_file.import_batch_id).update(
                    status=models.ImportStatus.FAILED,
                    error_message=str(exc),
                )
        if retried:
            self.stdout.write(f"Retried stale failed files: {retried}.")

        settings_obj = models.ImportSettings.get_solo()
        if not options["force"]:
            if not settings_obj.enabled:
                self.stdout.write("Import settings disabled. Use --force to run.")
                return
            if settings_obj.last_run_at:
                elapsed = timezone.now() - settings_obj.last_run_at
                if elapsed.total_seconds() < settings_obj.interval_minutes * 60:
                    self.stdout.write("Skipped. Last run too recent.")
                    return

        mailboxes = models.Mailbox.objects.filter(is_active=True)
        if options["mailbox"]:
            mailboxes = mailboxes.filter(name=options["mailbox"])

        limit = options["limit"] if options["limit"] else settings_obj.max_messages_per_run

        today = timezone.localdate()
        cbr_rate_exists_today = models.ExchangeRate.objects.filter(
            rate_date=today,
            from_currency=models.Currency.USD,
            to_currency=models.Currency.RUB,
            source__startswith="CBR + ",
        ).exists()
        if cbr_rate_exists_today:
            self.stdout.write("CBR daily rate already synced for today.")
        else:
            try:
                upsert_cbr_markup_rates(
                    today,
                    settings_obj.cbr_markup_percent,
                )
                self.stdout.write("CBR daily USD->RUB rate synced.")
            except Exception as exc:
                self.stdout.write(f"CBR rate sync skipped: {exc}")

        suppliers = list(
            models.Supplier.objects.filter(is_active=True, from_address_pattern__gt="")
            .order_by("name")
        )
        if not suppliers:
            settings_obj.last_run_at = timezone.now()
            settings_obj.save(update_fields=["last_run_at"])
            return

        batch_size = settings_obj.supplier_batch_size or len(suppliers)
        if batch_size >= len(suppliers):
            supplier_batch = suppliers
            next_offset = 0
        else:
            offset = settings_obj.supplier_batch_offset % len(suppliers)
            end = offset + batch_size
            if end <= len(suppliers):
                supplier_batch = suppliers[offset:end]
            else:
                supplier_batch = suppliers[offset:] + suppliers[: end - len(suppliers)]
            next_offset = (offset + batch_size) % len(suppliers)

        mailbox_names = ", ".join(mailboxes.values_list("name", flat=True))
        for supplier in supplier_batch:
            check_started = timezone.now()
            latest_batch = _get_supplier_latest_batch_time(supplier)
            if latest_batch and timezone.is_naive(latest_batch):
                latest_batch = timezone.make_aware(latest_batch)
            if latest_batch:
                # Look back a few days to avoid missing same-day or late-arriving emails.
                since_date = timezone.localtime(latest_batch) - timezone.timedelta(days=3)
            else:
                since_date = timezone.now() - timezone.timedelta(days=supplier.email_search_days)
            self.stdout.write(
                f"Checking supplier: {supplier.name} (since {since_date:%Y-%m-%d %H:%M})"
            )
            summary = run_import(
                mailboxes=mailboxes,
                supplier_id=supplier.id,
                mark_seen=False,
                limit=limit,
                max_bytes=options["max_bytes"],
                logger=self.stdout.write,
                search_criteria="ALL",
                since_date=since_date,
                # Don't hard-skip by latest batch time; rely on lookback window
                # + hash dedupe to avoid missing earlier same-day emails.
                min_received_at=None,
                from_filter=supplier.from_address_pattern or None,
                subject_filter=supplier.price_subject_pattern or None,
                dedupe_same_day_only=False,
            )
            supplier.last_email_check_at = check_started
            supplier.last_email_matched = summary.get("matched_files", 0)
            supplier.last_email_processed = summary.get("processed_files", 0)
            supplier.last_email_errors = summary.get("errors", 0)
            supplier.last_email_last_message = summary.get("last_message") or ""
            supplier.last_email_mailboxes = mailbox_names
            supplier.save(
                update_fields=[
                    "last_email_check_at",
                    "last_email_matched",
                    "last_email_processed",
                    "last_email_errors",
                    "last_email_last_message",
                    "last_email_mailboxes",
                ]
            )

        settings_obj.last_run_at = timezone.now()
        settings_obj.supplier_batch_offset = next_offset
        settings_obj.save(update_fields=["last_run_at", "supplier_batch_offset"])
