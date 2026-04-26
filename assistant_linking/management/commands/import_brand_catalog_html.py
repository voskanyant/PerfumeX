from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from assistant_linking.services.html_catalog_importer import (
    import_brand_catalog,
    parse_brand_catalog_file,
    write_missing_report,
)


class Command(BaseCommand):
    help = "Parse a saved brand catalogue HTML page and optionally update catalogue collections/aliases."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to saved HTML/text from a brand catalogue page.")
        parser.add_argument("--brand", help="Override detected brand name.")
        parser.add_argument("--source-url", default="", help="Original source URL to attach to matched/created perfumes.")
        parser.add_argument("--apply", action="store_true", help="Write catalogue updates. Default is dry-run.")
        parser.add_argument(
            "--create-missing-catalog",
            action="store_true",
            help="Create missing catalog.Perfume rows. Requires --apply.",
        )
        parser.add_argument(
            "--create-aliases",
            action="store_true",
            help="Create/update BrandAlias and ProductAlias rows for normalizer matching. Requires --apply.",
        )
        parser.add_argument("--missing-report", help="Write missing catalogue items to a CSV file.")
        parser.add_argument(
            "--reparse-supplier-products",
            action="store_true",
            help="After applying aliases, reparse supplier products for this brand so new catalogue aliases take effect.",
        )
        parser.add_argument(
            "--reparse-all-supplier-products",
            action="store_true",
            help="Reparse every supplier product after import. Use only when intentionally refreshing all parses.",
        )

    def handle(self, *args, **options):
        if options["create_missing_catalog"] and not options["apply"]:
            raise CommandError("--create-missing-catalog requires --apply.")
        if options["create_aliases"] and not options["apply"]:
            raise CommandError("--create-aliases requires --apply.")
        if (options["reparse_supplier_products"] or options["reparse_all_supplier_products"]) and not options["apply"]:
            raise CommandError("--reparse-supplier-products/--reparse-all-supplier-products requires --apply.")

        items = parse_brand_catalog_file(options["path"])
        summary = import_brand_catalog(
            items,
            brand_name=options["brand"],
            apply=options["apply"],
            create_missing_catalog=options["create_missing_catalog"],
            create_aliases=options["create_aliases"],
            source_url=options["source_url"],
        )
        if options["missing_report"]:
            write_missing_report(options["missing_report"], summary.missing_items)

        mode = "APPLY" if options["apply"] else "DRY-RUN"
        brand_name = summary.brand.name if summary.brand else options.get("brand") or "unknown"
        self.stdout.write(f"{mode}: {brand_name}")
        self.stdout.write(f"Source items: {len(summary.source_items)}")
        self.stdout.write(f"Collections: {len(summary.collections)}")
        for collection in sorted(summary.collections):
            self.stdout.write(f"  - {collection}")
        self.stdout.write(f"Matched catalogue perfumes: {len(summary.matched_perfumes)}")
        self.stdout.write(f"Missing catalogue perfumes: {len(summary.missing_items)}")
        self.stdout.write(f"Created catalogue perfumes: {len(summary.created_perfumes)}")
        self.stdout.write(f"Updated catalogue perfumes: {len(summary.updated_perfumes)}")
        self.stdout.write(f"Aliases created/updated: {summary.created_aliases}/{summary.updated_aliases}")
        self.stdout.write(f"Sources created: {summary.created_sources}")
        if options["missing_report"]:
            self.stdout.write(f"Missing report: {options['missing_report']}")

        if options["reparse_all_supplier_products"]:
            call_command("reparse_supplier_products")
        elif options["reparse_supplier_products"]:
            reparse_term = (summary.brand.name if summary.brand else options.get("brand") or "").split("&")[0].strip()
            call_command("reparse_supplier_products", "--name-contains", reparse_term)
