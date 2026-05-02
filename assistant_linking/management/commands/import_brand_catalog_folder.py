from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from assistant_linking.services.html_catalog_importer import (
    CatalogItem,
    import_brand_catalog,
    parse_brand_catalog_file,
    write_missing_report,
)


class Command(BaseCommand):
    help = "Parse a folder of saved or parsed Fragrantica catalogue files into staging rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "path", help="Folder containing saved Fragrantica brand HTML/text files."
        )
        parser.add_argument(
            "--pattern",
            action="append",
            default=[],
            help="Glob pattern to import. Can be repeated. Defaults to *.json, *.html, and *.htm.",
        )
        parser.add_argument(
            "--recursive",
            action="store_true",
            help="Search subfolders recursively.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write FragranticaProduct staging rows. Default is dry-run.",
        )
        parser.add_argument(
            "--missing-report",
            help="Write one CSV containing new staged-row candidates across all files.",
        )

    def handle(self, *args, **options):
        root = Path(options["path"])
        if not root.exists():
            raise CommandError(f"Folder does not exist: {console_path(root)}")
        if not root.is_dir():
            raise CommandError(f"Path is not a folder: {console_path(root)}")

        files = discover_catalog_files(
            root,
            patterns=options["pattern"] or ["*.json", "*.html", "*.htm"],
            recursive=options["recursive"],
        )
        if not files:
            raise CommandError(f"No catalogue files found in {console_path(root)}")

        mode = "APPLY" if options["apply"] else "DRY-RUN"
        total_source_items = 0
        total_existing = 0
        total_new = 0
        total_created = 0
        total_updated = 0
        missing_items: list[CatalogItem] = []
        parse_failures: list[tuple[Path, Exception]] = []

        self.stdout.write(f"{mode}: Fragrantica folder import")
        self.stdout.write(f"Files discovered: {len(files)}")
        for index, path in enumerate(files, start=1):
            try:
                items = parse_brand_catalog_file(path)
            except Exception as exc:  # noqa: BLE001 - report and continue.
                parse_failures.append((path, exc))
                self.stderr.write(
                    f"[{index}/{len(files)}] FAILED {console_path(path)}: {exc}"
                )
                continue

            summary = import_brand_catalog(items, apply=options["apply"])
            total_source_items += len(summary.source_items)
            total_existing += len(summary.existing_fragrantica_products)
            total_new += len(summary.missing_items)
            total_created += len(summary.created_fragrantica_products)
            total_updated += len(summary.updated_fragrantica_products)
            missing_items.extend(summary.missing_items)
            if options["verbosity"] > 1:
                brand_name = summary.brand_name or "unknown"
                self.stdout.write(
                    f"[{index}/{len(files)}] {brand_name}: "
                    f"{len(summary.source_items)} source, "
                    f"{len(summary.missing_items)} new"
                )

        if options["missing_report"]:
            write_missing_report(options["missing_report"], missing_items)

        self.stdout.write(f"Source items: {total_source_items}")
        self.stdout.write(f"Existing Fragrantica products: {total_existing}")
        self.stdout.write(f"New Fragrantica products: {total_new}")
        self.stdout.write(f"Created Fragrantica products: {total_created}")
        self.stdout.write(f"Updated Fragrantica products: {total_updated}")
        if options["missing_report"]:
            self.stdout.write(
                f"New-row report: {console_path(options['missing_report'])}"
            )
        if parse_failures:
            self.stdout.write("Parse failures:")
            for path, exc in parse_failures:
                self.stdout.write(
                    f"  - {console_path(path)}: {exc.__class__.__name__}: {exc}"
                )
            raise CommandError("Some Fragrantica catalogue files could not be parsed.")


def discover_catalog_files(
    root: Path,
    *,
    patterns: list[str],
    recursive: bool,
) -> list[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        globber = root.rglob if recursive else root.glob
        files.update(path for path in globber(pattern) if path.is_file())
    return sorted(files)


def console_path(value) -> str:
    return str(value).encode("ascii", errors="backslashreplace").decode("ascii")
