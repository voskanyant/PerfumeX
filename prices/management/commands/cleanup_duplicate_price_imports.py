from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from prices import models
from prices.services.importer import delete_import_batch


class Command(BaseCommand):
    help = (
        "Cleanup duplicate PRICE imports using rule: "
        "supplier + local day + content_hash (keep one, across mailboxes)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply deletions. Without this flag it is dry-run.",
        )
        parser.add_argument(
            "--supplier-id",
            type=int,
            default=0,
            help="Limit cleanup to one supplier id.",
        )
        parser.add_argument(
            "--start-date",
            type=str,
            default="",
            help="Local date lower bound (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--end-date",
            type=str,
            default="",
            help="Local date upper bound (YYYY-MM-DD).",
        )

    def handle(self, *args, **options):
        apply_mode = bool(options["apply"])
        supplier_id = int(options.get("supplier_id") or 0)
        start_date = self._parse_date(options.get("start_date") or "")
        end_date = self._parse_date(options.get("end_date") or "")

        qs = (
            models.ImportFile.objects.filter(
                file_kind=models.FileKind.PRICE,
                content_hash__isnull=False,
            )
            .exclude(content_hash="")
            .select_related("import_batch", "import_batch__supplier", "import_batch__mailbox")
            .order_by("id")
        )
        if supplier_id:
            qs = qs.filter(import_batch__supplier_id=supplier_id)

        files = list(qs)
        if not files:
            self.stdout.write("No price imports found.")
            return

        groups: dict[tuple[int, object, str], list[models.ImportFile]] = defaultdict(list)
        for f in files:
            batch = f.import_batch
            if not batch:
                continue
            at = batch.received_at or batch.created_at
            if at is None:
                continue
            local_day = timezone.localtime(at).date()
            if start_date and local_day < start_date:
                continue
            if end_date and local_day > end_date:
                continue
            key = (batch.supplier_id, local_day, f.content_hash)
            groups[key].append(f)

        if not groups:
            self.stdout.write("No matching files in selected scope.")
            return

        status_rank = {
            models.ImportStatus.PROCESSED: 2,
            models.ImportStatus.PENDING: 1,
            models.ImportStatus.FAILED: 0,
        }

        duplicate_files: list[models.ImportFile] = []
        keep_files = 0
        duplicate_groups = 0
        for key, members in groups.items():
            if len(members) <= 1:
                keep_files += 1
                continue
            duplicate_groups += 1
            members_sorted = sorted(
                members,
                key=lambda f: (
                    -status_rank.get(f.status, -1),
                    (f.import_batch.received_at or f.import_batch.created_at or timezone.now()),
                    f.id,
                ),
            )
            keep = members_sorted[0]
            keep_files += 1
            duplicate_files.extend(members_sorted[1:])
            supplier_name = keep.import_batch.supplier.name if keep.import_batch and keep.import_batch.supplier else "?"
            mailbox_name = keep.import_batch.mailbox.name if keep.import_batch and keep.import_batch.mailbox else "-"
            self.stdout.write(
                f"[dup-group] supplier={supplier_name} mailbox={mailbox_name} day={key[1]} hash={key[2][:12]}... keep_file_id={keep.id} drop={len(members_sorted)-1}"
            )

        if not duplicate_files:
            self.stdout.write("No duplicates found by current rule.")
            return

        batch_ids = sorted({f.import_batch_id for f in duplicate_files if f.import_batch_id})
        batch_file_counts = dict(
            models.ImportFile.objects.filter(import_batch_id__in=batch_ids)
            .values("import_batch_id")
            .annotate(c=Count("id"))
            .values_list("import_batch_id", "c")
        )

        unique_delete_batches = []
        skipped_non_single = 0
        seen_batch_ids = set()
        for f in duplicate_files:
            batch_id = f.import_batch_id
            if not batch_id or batch_id in seen_batch_ids:
                continue
            seen_batch_ids.add(batch_id)
            if batch_file_counts.get(batch_id, 0) != 1:
                skipped_non_single += 1
                continue
            unique_delete_batches.append(f.import_batch)

        self.stdout.write(
            f"Summary: groups={duplicate_groups} keep={keep_files} duplicate_files={len(duplicate_files)} "
            f"candidate_batches={len(unique_delete_batches)} skipped_non_single_batch={skipped_non_single}"
        )

        if not apply_mode:
            self.stdout.write("Dry run complete. Use --apply to delete duplicate batches.")
            return

        deleted = 0
        errors = 0
        for batch in unique_delete_batches:
            try:
                delete_import_batch(batch)
                deleted += 1
            except Exception as exc:
                errors += 1
                self.stdout.write(self.style.ERROR(f"[error] batch_id={batch.id}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Applied: deleted_batches={deleted} errors={errors} skipped_non_single_batch={skipped_non_single}"
            )
        )

    @staticmethod
    def _parse_date(value: str):
        text = (value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise SystemExit(f"Invalid date format: {text}. Expected YYYY-MM-DD")
