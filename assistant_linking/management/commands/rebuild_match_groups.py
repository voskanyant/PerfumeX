from django.core.management.base import BaseCommand

from assistant_linking.services.grouping import rebuild_groups


class Command(BaseCommand):
    help = "Rebuild deterministic cross-supplier match groups."

    def add_arguments(self, parser):
        parser.add_argument("--supplier-id", type=int)
        parser.add_argument("--only-open", action="store_true")

    def handle(self, *args, **options):
        count = rebuild_groups(
            supplier_id=options["supplier_id"],
            only_open=options["only_open"],
        )
        self.stdout.write(self.style.SUCCESS(f"Rebuilt {count} group memberships."))
