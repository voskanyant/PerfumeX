from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from assistant_linking.services.html_catalog_importer import (
    import_brand_catalog,
    parse_brand_catalog_file,
    write_missing_report,
)


class Command(BaseCommand):
    help = "Parse a saved brand catalogue HTML page into the external Fragrantica staging catalogue."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to saved HTML/text from a brand catalogue page.")
        parser.add_argument("--brand", help="Override detected brand name.")
        parser.add_argument("--source-url", default="", help="Original source URL to attach to staged Fragrantica rows.")
        parser.add_argument("--apply", action="store_true", help="Write FragranticaProduct staging rows. Default is dry-run.")
        parser.add_argument(
            "--create-missing-catalog",
            action="store_true",
            help="Deprecated. Fragrantica rows are staged separately and merged after review.",
        )
        parser.add_argument(
            "--create-aliases",
            action="store_true",
            help="Deprecated. Aliases are created after review/linking, not during HTML import.",
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
        if options["create_missing_catalog"]:
            raise CommandError("--create-missing-catalog is disabled. Import Fragrantica rows first, then merge after review.")
        if options["create_aliases"]:
            raise CommandError("--create-aliases is disabled. Link/merge Fragrantica rows before creating normalizer knowledge.")
        if options["reparse_supplier_products"] or options["reparse_all_supplier_products"]:
            raise CommandError("HTML import only stages Fragrantica rows. Reparse after approved link/merge knowledge changes.")

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
        brand_name = summary.brand_name or options.get("brand") or "unknown"
        self.stdout.write(f"{mode}: {brand_name}")
        self.stdout.write(f"Source items: {len(summary.source_items)}")
        self.stdout.write(f"Collections: {len(summary.collections)}")
        for collection in sorted(summary.collections):
            self.stdout.write(f"  - {collection}")
        audience_counts = {}
        for item in summary.source_items:
            if item.audience:
                audience_counts[item.audience] = audience_counts.get(item.audience, 0) + 1
        if audience_counts:
            self.stdout.write("Audiences:")
            for audience, count in sorted(audience_counts.items()):
                self.stdout.write(f"  - {audience}: {count}")
        self.stdout.write(f"Existing Fragrantica products: {len(summary.existing_fragrantica_products)}")
        self.stdout.write(f"New Fragrantica products: {len(summary.missing_items)}")
        self.stdout.write(f"Created Fragrantica products: {len(summary.created_fragrantica_products)}")
        self.stdout.write(f"Updated Fragrantica products: {len(summary.updated_fragrantica_products)}")
        if options["missing_report"]:
            self.stdout.write(f"New-row report: {options['missing_report']}")
