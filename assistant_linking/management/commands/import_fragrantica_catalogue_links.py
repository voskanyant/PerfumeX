from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from assistant_linking.services.catalogue_promotion import (
    import_fragrantica_catalogue_link_export,
)


class Command(BaseCommand):
    help = (
        "Import reviewed Fragrantica catalogue links from another PerfumeX environment."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "path", help="Source JSON file from export_fragrantica_catalogue_links."
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write changes. Default is dry-run.",
        )
        parser.add_argument(
            "--create-missing-perfumes",
            action="store_true",
            help="Create missing target catalogue perfumes. Default skips missing targets.",
        )

    def handle(self, *args, **options):
        try:
            summary = import_fragrantica_catalogue_link_export(
                options["path"],
                apply=options["apply"],
                create_missing_perfumes=options["create_missing_perfumes"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(f"{mode}: reviewed Fragrantica catalogue links")
        self.stdout.write(f"Rows scanned: {summary.scanned}")
        self.stdout.write(f"Rows linked: {summary.linked_sources}")
        self.stdout.write(f"Sources created: {summary.created_sources}")
        self.stdout.write(f"Sources updated: {summary.updated_sources}")
        self.stdout.write(f"Perfumes created: {summary.created_perfumes}")
        self.stdout.write(f"Perfumes updated: {summary.updated_perfumes}")
        self.stdout.write(f"Rows skipped: {summary.skipped}")
        if summary.issues:
            self.stdout.write("Issues:")
            for issue in summary.issues:
                self.stdout.write(f"  - row {issue.row}: {issue.message}")
            raise CommandError("Import finished with unresolved issues.")
