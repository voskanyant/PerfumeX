from __future__ import annotations

from datetime import datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from prices import models
from prices.management.commands.import_emails import _get_supplier_latest_batch_time
from prices.services.email_importer import run_import
from prices.services.email_import_lock import acquire_email_import_worker_lock


class Command(BaseCommand):
    help = "Process one or more EmailImportRun records in a detached worker process."

    def add_arguments(self, parser):
        parser.add_argument(
            "--run-id",
            action="append",
            type=int,
            dest="run_ids",
            help="EmailImportRun id to process. Repeat for multiple runs.",
        )
        parser.add_argument(
            "--mark-seen",
            action="store_true",
            help="Mark processed emails as seen for standard update runs.",
        )
        parser.add_argument(
            "--start-date",
            help="Optional backfill start date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--end-date",
            help="Optional backfill end date in YYYY-MM-DD format.",
        )

    def handle(self, *args, **options):
        run_ids = list(dict.fromkeys(options.get("run_ids") or []))
        if not run_ids:
            self.stdout.write("No run ids provided.")
            return

        start_date = self._parse_date(options.get("start_date"), "start date")
        end_date = self._parse_date(options.get("end_date"), "end date")
        if start_date and end_date and end_date < start_date:
            raise CommandError("End date must be on or after start date.")

        lock_cm = acquire_email_import_worker_lock()
        acquired = lock_cm.__enter__()
        try:
            if not acquired:
                message = "Skipped: another email import worker is already active."
                self._fail_runs(run_ids, message)
                self.stdout.write(message)
                return

            close_old_connections()
            mailboxes = list(
                models.Mailbox.objects.filter(is_active=True).order_by("priority", "id")
            )
            if not mailboxes:
                self._fail_runs(run_ids, "No active mailboxes configured.")
                self.stdout.write("No active mailboxes configured.")
                return

            settings_obj = models.ImportSettings.get_solo()
            timeout_minutes = int(settings_obj.supplier_timeout_minutes or 0)
            timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
            limit = int(settings_obj.max_messages_per_run or 0)
            mark_seen = bool(options.get("mark_seen"))

            runs = list(
                models.EmailImportRun.objects.select_related("supplier")
                .filter(id__in=run_ids)
                .order_by("started_at", "id")
            )
            found_ids = {run.id for run in runs}
            missing_ids = [run_id for run_id in run_ids if run_id not in found_ids]
            if missing_ids:
                self.stdout.write(
                    f"Skipping missing run ids: {', '.join(str(value) for value in missing_ids)}"
                )

            for run in runs:
                supplier = run.supplier
                if run.status == models.EmailImportStatus.CANCELED:
                    if not run.finished_at:
                        models.EmailImportRun.objects.filter(id=run.id).update(
                            finished_at=timezone.now()
                        )
                    continue
                if not supplier.is_active:
                    self._fail_run(run.id, "Supplier is inactive.")
                    continue
                if not supplier.from_address_pattern:
                    self._fail_run(run.id, "Supplier has no sender email configured.")
                    continue

                try:
                    if start_date:
                        since_date = timezone.make_aware(
                            datetime.combine(start_date, time(0, 0))
                        )
                        before_date = None
                        if end_date:
                            before_date = timezone.make_aware(
                                datetime.combine(
                                    end_date + timezone.timedelta(days=1), time(0, 0)
                                )
                            )
                        run_import(
                            mailboxes=mailboxes,
                            supplier_id=supplier.id,
                            mark_seen=False,
                            limit=0,
                            max_bytes=20_000_000,
                            max_seconds=timeout_seconds,
                            logger=None,
                            run_id=run.id,
                            search_criteria="ALL",
                            since_date=since_date,
                            before_date=before_date,
                            from_filter=supplier.from_address_pattern or None,
                            subject_filter=supplier.price_subject_pattern or None,
                            dedupe_same_day_only=True,
                            dedupe_day_window=3,
                        )
                    else:
                        latest_batch = _get_supplier_latest_batch_time(supplier)
                        if latest_batch and timezone.is_naive(latest_batch):
                            latest_batch = timezone.make_aware(latest_batch)
                        if latest_batch:
                            since_date = timezone.localtime(latest_batch) - timezone.timedelta(
                                days=1
                            )
                        else:
                            since_date = timezone.now() - timezone.timedelta(
                                days=supplier.email_search_days
                            )
                        run_import(
                            mailboxes=mailboxes,
                            supplier_id=supplier.id,
                            mark_seen=mark_seen,
                            limit=limit,
                            max_bytes=20_000_000,
                            max_seconds=timeout_seconds,
                            logger=None,
                            run_id=run.id,
                            search_criteria="ALL",
                            since_date=since_date,
                            min_received_at=latest_batch,
                            from_filter=supplier.from_address_pattern or None,
                            subject_filter=supplier.price_subject_pattern or None,
                            dedupe_same_day_only=True,
                        )
                except Exception as exc:
                    self._fail_run(run.id, str(exc))
        finally:
            lock_cm.__exit__(None, None, None)

    def _parse_date(self, value, label):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).date()
        except ValueError as exc:
            raise CommandError(f"Invalid {label}: {value}") from exc

    def _fail_run(self, run_id: int, message: str):
        models.EmailImportRun.objects.filter(id=run_id).update(
            status=models.EmailImportStatus.FAILED,
            finished_at=timezone.now(),
            errors=1,
            last_message=message,
        )

    def _fail_runs(self, run_ids: list[int], message: str):
        models.EmailImportRun.objects.filter(id__in=run_ids).update(
            status=models.EmailImportStatus.FAILED,
            finished_at=timezone.now(),
            errors=1,
            last_message=message,
        )
