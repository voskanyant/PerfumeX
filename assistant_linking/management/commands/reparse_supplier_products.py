from django.core.management.base import BaseCommand

from assistant_linking.services.normalizer import save_parse
from prices.models import SupplierProduct


class Command(BaseCommand):
    help = "Deterministically reparse supplier products into ParsedSupplierProduct rows."

    def add_arguments(self, parser):
        parser.add_argument("--supplier-id", type=int)
        parser.add_argument("--only-unparsed", action="store_true")

    def handle(self, *args, **options):
        queryset = SupplierProduct.objects.select_related("supplier").all()
        if options["supplier_id"]:
            queryset = queryset.filter(supplier_id=options["supplier_id"])
        if options["only_unparsed"]:
            queryset = queryset.filter(assistant_parse__isnull=True)
        count = 0
        for product in queryset.iterator():
            save_parse(product)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Parsed {count} supplier products."))
