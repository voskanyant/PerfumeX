from __future__ import annotations

from django.core.management.base import BaseCommand

from assistant_linking.services.catalogue_promotion import (
    write_fragrantica_catalogue_link_export,
)


class Command(BaseCommand):
    help = "Export reviewed Fragrantica catalogue links for promotion to another environment."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Destination JSON file.")
        parser.add_argument(
            "--brand",
            default="",
            help="Optional Fragrantica brand name filter.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Optional maximum number of linked rows to export.",
        )

    def handle(self, *args, **options):
        summary = write_fragrantica_catalogue_link_export(
            options["path"],
            brand_name=options["brand"],
            limit=options["limit"],
        )
        self.stdout.write(f"Exported reviewed Fragrantica links: {summary.exported}")
        self.stdout.write(f"File: {options['path']}")
