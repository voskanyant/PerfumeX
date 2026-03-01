from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import F
from django.db.models import Q
from django.db.models import Count

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
    PRODUCT_REMOVED_EVENT_PREFIX = "SYSTEM_DEACTIVATE:"

    def add_arguments(self, parser):
        parser.add_argument("--mailbox", help="Mailbox name to process.")
        parser.add_argument(
            "--supplier-id",
            type=int,
            help="Process only this supplier id.",
        )
        parser.add_argument(
            "--run-id",
            type=int,
            help="Existing EmailImportRun id to update progress for.",
        )
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
        timeout_minutes = int(settings_obj.supplier_timeout_minutes or 0)
        timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
        if timeout_minutes > 0:
            timeout_cutoff = timezone.now() - timezone.timedelta(minutes=timeout_minutes)
            stale_runs = models.EmailImportRun.objects.filter(
                status=models.EmailImportStatus.RUNNING,
                started_at__lt=timeout_cutoff,
            )
            if stale_runs.exists():
                stale_runs.update(
                    status=models.EmailImportStatus.FAILED,
                    finished_at=timezone.now(),
                    errors=F("errors") + 1,
                    last_message="Auto-failed timeout. Previous run exceeded supplier timeout.",
                )
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
        mailbox_list = list(mailboxes)
        if mailbox_list:
            self.stdout.write(
                "Active mailboxes: "
                + ", ".join([f"{m.name}({m.protocol})" for m in mailbox_list])
            )
        else:
            self.stdout.write("No active mailboxes configured.")
            return

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

        supplier_id = options.get("supplier_id")
        run_id = options.get("run_id")
        if supplier_id:
            supplier = models.Supplier.objects.filter(
                id=supplier_id, is_active=True
            ).first()
            if not supplier:
                self.stdout.write("Supplier not found or inactive.")
                if run_id:
                    models.EmailImportRun.objects.filter(id=run_id).update(
                        status=models.EmailImportStatus.FAILED,
                        finished_at=timezone.now(),
                        errors=F("errors") + 1,
                        last_message="Supplier not found or inactive.",
                    )
                return
            if not supplier.from_address_pattern:
                self.stdout.write("Supplier has no sender email configured.")
                if run_id:
                    models.EmailImportRun.objects.filter(id=run_id).update(
                        status=models.EmailImportStatus.FAILED,
                        finished_at=timezone.now(),
                        errors=F("errors") + 1,
                        last_message="Supplier has no sender email configured.",
                    )
                return

            latest_batch = _get_supplier_latest_batch_time(supplier)
            if latest_batch and timezone.is_naive(latest_batch):
                latest_batch = timezone.make_aware(latest_batch)
            if latest_batch:
                since_date = timezone.localtime(latest_batch) - timezone.timedelta(days=1)
            else:
                base_days = supplier.email_search_days or 7
                since_date = timezone.now() - timezone.timedelta(days=base_days)
            self.stdout.write(
                f"Checking supplier {supplier.name} (since {since_date:%Y-%m-%d %H:%M})"
            )
            summary = run_import(
                mailboxes=mailbox_list,
                supplier_id=supplier.id,
                mark_seen=False,
                limit=limit,
                max_bytes=options["max_bytes"],
                max_seconds=timeout_seconds,
                logger=self.stdout.write,
                run_id=run_id,
                search_criteria="ALL",
                since_date=since_date,
                min_received_at=latest_batch,
                from_filter=supplier.from_address_pattern or None,
                subject_filter=supplier.price_subject_pattern or None,
                dedupe_same_day_only=True,
                use_uid_cursor=not options["force"],
            )
            self.stdout.write(
                "Import summary: "
                f"matched={summary.get('matched_files', 0)} "
                f"processed={summary.get('processed_files', 0)} "
                f"duplicates={summary.get('skipped_duplicates', 0)} "
                f"errors={summary.get('errors', 0)} "
                f"timed_out={summary.get('timed_out', False)}"
            )
            return

        max_days = max([s.email_search_days for s in suppliers] or [7])
        if options["force"]:
            since_date = timezone.now() - timezone.timedelta(days=max(max_days, 7))
        elif settings_obj.last_run_at:
            since_date = timezone.localtime(settings_obj.last_run_at) - timezone.timedelta(days=3)
        else:
            since_date = timezone.now() - timezone.timedelta(days=max_days)

        self.stdout.write(f"Checking mailboxes (since {since_date:%Y-%m-%d %H:%M})")
        summary = run_import(
            mailboxes=mailbox_list,
            supplier_id=None,
            mark_seen=False,
            limit=limit,
            max_bytes=options["max_bytes"],
            max_seconds=timeout_seconds,
            logger=self.stdout.write,
            search_criteria="ALL",
            since_date=since_date,
            min_received_at=None,
            from_filter=None,
            subject_filter=None,
            dedupe_same_day_only=True,
            use_uid_cursor=not options["force"],
        )
        self.stdout.write(
            "Import summary: "
            f"matched={summary.get('matched_files', 0)} "
            f"processed={summary.get('processed_files', 0)} "
            f"duplicates={summary.get('skipped_duplicates', 0)} "
            f"errors={summary.get('errors', 0)} "
            f"timed_out={summary.get('timed_out', False)}"
        )

        stale_days = int(settings_obj.deactivate_products_after_days or 0)
        if stale_days > 0:
            cutoff = timezone.now() - timezone.timedelta(days=stale_days)
            stale_qs = models.SupplierProduct.objects.select_related("supplier").filter(
                is_active=True,
            ).filter(
                Q(last_imported_at__lt=cutoff)
                | Q(last_imported_at__isnull=True, created_at__lt=cutoff)
            )
            by_supplier = list(
                stale_qs.values("supplier_id").annotate(total=Count("id"))
            )
            deactivated = stale_qs.update(is_active=False)
            if deactivated:
                now = timezone.now()
                for row in by_supplier:
                    supplier_id = row.get("supplier_id")
                    total = row.get("total") or 0
                    if not supplier_id or total <= 0:
                        continue
                    supplier = models.Supplier.objects.filter(pk=supplier_id).first()
                    if not supplier:
                        continue
                    models.ImportBatch.objects.create(
                        supplier=supplier,
                        mailbox=None,
                        message_id=f"{self.PRODUCT_REMOVED_EVENT_PREFIX}{now.isoformat()}:{supplier_id}",
                        received_at=now,
                        status=models.ImportStatus.PROCESSED,
                        error_message=(
                            f"No price received for {stale_days} day(s). "
                            f"Deactivated {total} supplier product(s)."
                        ),
                    )
            self.stdout.write(
                f"Auto-deactivated stale supplier products: {deactivated} (older than {stale_days} days)."
            )

        settings_obj.last_run_at = timezone.now()
        settings_obj.save(update_fields=["last_run_at"])
