from __future__ import annotations

from pathlib import Path

from django.core.files.base import File
from django.core.management.base import BaseCommand

from prices import models


class Command(BaseCommand):
    help = (
        "Move existing ImportFile stored files into organized supplier folders and "
        "prefix each filename with received datetime."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned moves only; do not modify files or database.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        verbosity = int(options.get("verbosity", 1))
        moved = 0
        planned = 0
        skipped = 0
        missing = 0
        errors = 0

        queryset = (
            models.ImportFile.objects.exclude(file="")
            .select_related("import_batch", "import_batch__supplier")
            .order_by("id")
        )
        total = queryset.count()
        self.stdout.write(f"Found {total} import files with stored blobs.")

        for import_file in queryset.iterator():
            old_name = (import_file.file.name or "").strip()
            if not old_name:
                skipped += 1
                continue

            old_base = Path(old_name).name
            desired_name = models.build_import_file_path(import_file, old_base)

            # Already organized (same final path pattern).
            if old_name == desired_name:
                skipped += 1
                continue

            storage = import_file.file.storage
            if not storage.exists(old_name):
                missing += 1
                if verbosity >= 1:
                    self.stdout.write(
                        self.style.WARNING(
                            self._safe_text(
                                f"[missing] ImportFile#{import_file.id} source not found: {old_name}"
                            )
                        )
                    )
                continue

            if verbosity >= 2:
                self.stdout.write(
                    self._safe_text(
                        f"[move] ImportFile#{import_file.id}: {old_name} -> {desired_name}"
                    )
                )
            if dry_run:
                planned += 1
                continue

            try:
                with storage.open(old_name, "rb") as source_file:
                    saved_name = storage.save(desired_name, File(source_file))
                storage.delete(old_name)

                import_file.file.name = saved_name
                import_file.filename = Path(saved_name).name
                import_file.save(update_fields=["file", "filename"])
                moved += 1
            except Exception as exc:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(
                        self._safe_text(
                            f"[error] ImportFile#{import_file.id}: {old_name} -> {desired_name} :: {exc}"
                        )
                    )
                )

        summary = (
            f"done moved={moved} planned={planned} skipped={skipped} missing={missing} errors={errors}"
        )
        if dry_run:
            summary = "DRY RUN " + summary
        self.stdout.write(self.style.SUCCESS(summary))

    @staticmethod
    def _safe_text(value: str) -> str:
        return str(value).encode("utf-8", errors="replace").decode(
            "utf-8", errors="replace"
        )
