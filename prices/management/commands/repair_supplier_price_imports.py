from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from prices import models
from prices.services.importer import process_import_file


class Command(BaseCommand):
    help = (
        "Reprocess stored price import files for a supplier/date range to repair "
        "incorrectly parsed prices."
    )

    def add_arguments(self, parser):
        parser.add_argument("--supplier-id", type=int, required=False)
        parser.add_argument("--all-suppliers", action="store_true")
        parser.add_argument("--date-from", type=str, required=False)
        parser.add_argument("--date-to", type=str, required=False)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        supplier_id = options["supplier_id"]
        all_suppliers = bool(options.get("all_suppliers"))
        date_from = options.get("date_from")
        date_to = options.get("date_to")
        dry_run = bool(options.get("dry_run"))

        if all_suppliers and supplier_id:
            raise CommandError("Use either --supplier-id or --all-suppliers, not both.")
        if not all_suppliers and not supplier_id:
            raise CommandError("Provide --supplier-id or use --all-suppliers.")

        if all_suppliers:
            base_qs = models.ImportFile.objects.filter(
                file_kind=models.FileKind.PRICE,
                status=models.ImportStatus.PROCESSED,
            )
            if date_from:
                base_qs = base_qs.filter(import_batch__received_at__date__gte=date_from)
            if date_to:
                base_qs = base_qs.filter(import_batch__received_at__date__lte=date_to)
            supplier_ids = list(
                base_qs.order_by()
                .values_list("import_batch__supplier_id", flat=True)
                .distinct()
            )
            if not supplier_ids:
                self.stdout.write("No suppliers found with processed price files in range.")
                return
        else:
            supplier = models.Supplier.objects.filter(pk=supplier_id).first()
            if not supplier:
                raise CommandError(f"Supplier with id={supplier_id} not found.")
            supplier_ids = [supplier_id]

        grand_ok = 0
        grand_failed = 0
        grand_total = 0

        for current_supplier_id in supplier_ids:
            supplier = models.Supplier.objects.filter(pk=current_supplier_id).first()
            if not supplier:
                continue

            qs = (
                models.ImportFile.objects.filter(
                    file_kind=models.FileKind.PRICE,
                    status=models.ImportStatus.PROCESSED,
                    import_batch__supplier_id=current_supplier_id,
                )
                .select_related("import_batch", "mapping")
                .order_by("import_batch__received_at", "id")
            )
            if date_from:
                qs = qs.filter(import_batch__received_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(import_batch__received_at__date__lte=date_to)

            import_files = list(qs)
            total = len(import_files)
            grand_total += total
            self.stdout.write(
                f"Supplier={supplier.name} (id={supplier.id}) files_to_reprocess={total} dry_run={dry_run}"
            )
            if not import_files:
                continue

            ok = 0
            failed = 0
            for idx, import_file in enumerate(import_files, start=1):
                batch = import_file.import_batch
                received_at = batch.received_at
                prefix = (
                    f"[{idx}/{total}] ImportFile#{import_file.id} "
                    f"batch#{batch.id} received_at={received_at}"
                )

                if not import_file.file:
                    failed += 1
                    self.stdout.write(f"{prefix} -> SKIP (missing stored file)")
                    continue
                if not import_file.mapping:
                    failed += 1
                    self.stdout.write(f"{prefix} -> SKIP (missing mapping)")
                    continue

                if dry_run:
                    self.stdout.write(f"{prefix} -> would reprocess")
                    ok += 1
                    continue

                try:
                    with transaction.atomic():
                        # Prevent duplicate history rows for this batch/supplier before reparse.
                        models.PriceSnapshot.objects.filter(
                            import_batch=batch,
                            supplier_product__supplier_id=current_supplier_id,
                        ).delete()
                        process_import_file(import_file)
                    ok += 1
                    self.stdout.write(f"{prefix} -> OK")
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    self.stdout.write(f"{prefix} -> ERROR: {exc}")

            grand_ok += ok
            grand_failed += failed
            self.stdout.write(
                f"Supplier done: {supplier.name} ok={ok} failed={failed} total={total}"
            )

        self.stdout.write(
            f"Done: suppliers={len(supplier_ids)} total_files={grand_total} ok={grand_ok} failed={grand_failed}"
        )
