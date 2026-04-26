from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from assistant_linking.services.normalizer import PARSER_VERSION, save_parse
from prices.models import SupplierProduct


class Command(BaseCommand):
    help = "Deterministically reparse supplier products into ParsedSupplierProduct rows."

    def add_arguments(self, parser):
        parser.add_argument("--supplier-id", type=int)
        parser.add_argument("--only-unparsed", action="store_true")
        parser.add_argument("--only-stale", action="store_true")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        if options["only_unparsed"] and options["only_stale"]:
            raise CommandError("--only-unparsed and --only-stale cannot be used together.")

        queryset = SupplierProduct.objects.select_related("supplier").all()
        if options["supplier_id"]:
            queryset = queryset.filter(supplier_id=options["supplier_id"])
        if options["only_unparsed"]:
            queryset = queryset.filter(assistant_parse__isnull=True)
        if options["only_stale"]:
            stale_filter = (
                Q(assistant_parse__parser_version__isnull=True)
                | ~Q(assistant_parse__parser_version=PARSER_VERSION)
                | Q(assistant_parse__last_parsed_at__isnull=True)
                | Q(updated_at__gt=F("assistant_parse__last_parsed_at"))
            )
            queryset = queryset.filter(assistant_parse__isnull=False, assistant_parse__locked_by_human=False).filter(stale_filter)

        queryset = queryset.order_by("id")
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        count = 0
        for product in queryset.iterator():
            save_parse(product)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Parsed {count} supplier products."))
