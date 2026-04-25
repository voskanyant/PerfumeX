from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from prices import models


class Command(BaseCommand):
    help = "Report or clean import media quarantine files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete expired quarantine files. Without this, command is dry-run.",
        )
        parser.add_argument(
            "--report-orphans",
            action="store_true",
            help="Report files under media/imports* that are not referenced by ImportFile.",
        )
        parser.add_argument(
            "--report-missing",
            action="store_true",
            help="Report ImportFile rows whose file is missing on disk.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        delete = bool(options["delete"])
        expired = models.ImportFile.objects.filter(
            storage_type=models.ImportFileStorage.QUARANTINE,
            quarantine_until__isnull=False,
            quarantine_until__lt=now,
        ).exclude(file="")
        expired_count = expired.count()
        deleted_count = 0
        for import_file in expired:
            path = import_file.file.path if import_file.file else ""
            self.stdout.write(f"expired quarantine: {import_file.id} {path}")
            if delete and import_file.file:
                import_file.file.delete(save=False)
                import_file.file = None
                import_file.save(update_fields=["file"])
                deleted_count += 1
        self.stdout.write(
            f"Expired quarantine files: {expired_count}; deleted: {deleted_count}; dry_run={not delete}"
        )

        referenced = {
            Path(name).as_posix()
            for name in models.ImportFile.objects.exclude(file="").values_list("file", flat=True)
            if name
        }

        if options["report_missing"]:
            missing = 0
            for import_file in models.ImportFile.objects.exclude(file=""):
                if import_file.file and not Path(import_file.file.path).exists():
                    missing += 1
                    self.stdout.write(f"missing db file: {import_file.id} {import_file.file.name}")
            self.stdout.write(f"Missing referenced files: {missing}")

        if options["report_orphans"]:
            media_root = Path(settings.MEDIA_ROOT)
            roots = [media_root / "imports", media_root / "imports_quarantine"]
            orphaned = 0
            for root in roots:
                if not root.exists():
                    continue
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(media_root).as_posix()
                    if rel not in referenced:
                        orphaned += 1
                        self.stdout.write(f"orphan media file: {rel}")
            self.stdout.write(f"Orphan media files: {orphaned}")
