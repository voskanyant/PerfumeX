from __future__ import annotations

import hashlib
import re
from datetime import datetime, time
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from prices import models
from prices.services.importer import process_import_file


SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
FILENAME_DT_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?:[_\s](?P<time>\d{2}-\d{2}-\d{2}))?",
    re.IGNORECASE,
)


def _parse_received_at_from_filename(filename: str):
    match = FILENAME_DT_RE.match(filename or "")
    if not match:
        return None
    date_part = match.group("date")
    time_part = match.group("time") or "00-00-00"
    try:
        dt = datetime.fromisoformat(f"{date_part} {time_part.replace('-', ':')}")
    except ValueError:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _local_day_window_bounds(dt, window_days=0):
    if not dt:
        return None, None
    safe_window = max(int(window_days or 0), 0)
    local_dt = timezone.localtime(dt)
    day_start_local = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start_local = day_start_local - timezone.timedelta(days=safe_window)
    window_end_local = day_start_local + timezone.timedelta(days=safe_window + 1)
    return window_start_local, window_end_local


class Command(BaseCommand):
    help = "Bulk import all price files from a server folder for one supplier."

    def add_arguments(self, parser):
        parser.add_argument("--supplier-id", type=int, required=True)
        parser.add_argument("--folder", type=str, required=True)
        parser.add_argument("--recursive", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--dedupe-window-days",
            type=int,
            default=3,
            help="Hash duplicate window around file date (default: 3 => +/-3 days).",
        )

    def handle(self, *args, **options):
        supplier_id = int(options["supplier_id"])
        folder = Path(options["folder"])
        recursive = bool(options["recursive"])
        dry_run = bool(options["dry_run"])
        dedupe_window_days = max(int(options["dedupe_window_days"] or 0), 0)

        supplier = models.Supplier.objects.filter(id=supplier_id).first()
        if not supplier:
            raise CommandError(f"Supplier id={supplier_id} not found.")

        mapping = (
            models.SupplierFileMapping.objects.filter(
                supplier=supplier,
                file_kind=models.FileKind.PRICE,
                is_active=True,
            )
            .order_by("-id")
            .first()
        )
        if not mapping:
            raise CommandError(
                f"No active PRICE mapping for supplier '{supplier.name}' (id={supplier.id})."
            )

        if not folder.exists() or not folder.is_dir():
            raise CommandError(f"Folder not found: {folder}")

        pattern = "**/*" if recursive else "*"
        files = [
            p
            for p in folder.glob(pattern)
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if not files:
            self.stdout.write("No supported files found.")
            return

        files = sorted(
            files,
            key=lambda p: (
                _parse_received_at_from_filename(p.name) or timezone.make_aware(
                    datetime.combine(datetime.max.date(), time(0, 0))
                ),
                p.name.lower(),
            ),
        )

        total = len(files)
        processed = 0
        skipped_dup = 0
        failed = 0
        skipped_no_date = 0

        self.stdout.write(
            f"Supplier={supplier.name} (id={supplier.id}) files={total} "
            f"dry_run={dry_run} dedupe_window_days={dedupe_window_days}"
        )

        for idx, path in enumerate(files, start=1):
            received_at = _parse_received_at_from_filename(path.name)
            if not received_at:
                skipped_no_date += 1
                self.stdout.write(
                    f"[{idx}/{total}] SKIP no date in filename: {path.name}"
                )
                continue

            with path.open("rb") as f:
                payload = f.read()
            content_hash = hashlib.sha256(payload).hexdigest()

            day_start_local, day_end_local = _local_day_window_bounds(
                received_at, dedupe_window_days
            )
            exists = models.ImportFile.objects.filter(
                content_hash=content_hash,
                file_kind=models.FileKind.PRICE,
                import_batch__supplier=supplier,
                import_batch__received_at__gte=day_start_local,
                import_batch__received_at__lt=day_end_local,
                status__in=[models.ImportStatus.PENDING, models.ImportStatus.PROCESSED],
            ).exists()
            if exists:
                skipped_dup += 1
                self.stdout.write(
                    f"[{idx}/{total}] SKIP duplicate hash in +/-{dedupe_window_days}d: {path.name}"
                )
                continue

            if dry_run:
                self.stdout.write(f"[{idx}/{total}] WOULD IMPORT: {path.name}")
                processed += 1
                continue

            import_batch = models.ImportBatch.objects.create(
                supplier=supplier,
                status=models.ImportStatus.PENDING,
                received_at=received_at,
            )
            import_file = models.ImportFile.objects.create(
                import_batch=import_batch,
                mapping=mapping,
                file_kind=models.FileKind.PRICE,
                filename=path.name,
                content_hash=content_hash,
                status=models.ImportStatus.PENDING,
            )

            try:
                with path.open("rb") as f:
                    import_file.file.save(path.name, File(f), save=True)
                process_import_file(import_file)
                import_file.status = models.ImportStatus.PROCESSED
                import_file.save(update_fields=["status"])
                import_batch.status = models.ImportStatus.PROCESSED
                import_batch.save(update_fields=["status"])
                processed += 1
                self.stdout.write(f"[{idx}/{total}] OK: {path.name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                import_file.status = models.ImportStatus.FAILED
                import_file.error_message = str(exc)
                import_file.save(update_fields=["status", "error_message"])
                import_batch.status = models.ImportStatus.FAILED
                import_batch.error_message = str(exc)
                import_batch.save(update_fields=["status", "error_message"])
                self.stdout.write(f"[{idx}/{total}] ERROR: {path.name} :: {exc}")

        self.stdout.write(
            f"Done total={total} imported={processed} skipped_dup={skipped_dup} "
            f"skipped_no_date={skipped_no_date} failed={failed}"
        )

