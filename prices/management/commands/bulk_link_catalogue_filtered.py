from django.core.management.base import BaseCommand, CommandError
from django.http import QueryDict

from prices.services.catalog_review import run_catalogue_linking_filtered_bulk_action


class Command(BaseCommand):
    help = "Bulk link safe Fragrantica suggestions matching catalogue linking filters."

    def add_arguments(self, parser):
        parser.add_argument("--brand", default="")
        parser.add_argument("--q", default="")
        parser.add_argument("--status", default="unlinked")
        parser.add_argument("--suggestions", default="with")
        parser.add_argument("--confidence", default="100")

    def handle(self, *args, **options):
        post_data = QueryDict("", mutable=True)
        post_data["action"] = "bulk_link_filtered"
        post_data["next"] = "/admin/our-products/linking/"
        for key in ("brand", "q", "status", "suggestions", "confidence"):
            post_data[key] = str(options.get(key) or "")

        self.stdout.write(
            "Starting all-filtered catalogue linking "
            f"(status={post_data['status']}, suggestions={post_data['suggestions']}, "
            f"confidence={post_data['confidence']}, brand={post_data['brand'] or 'all'}, "
            f"q={post_data['q'] or '-'})..."
        )
        result = run_catalogue_linking_filtered_bulk_action(post_data)
        if result.level == "error":
            raise CommandError(result.message)
        self.stdout.write(self.style.SUCCESS(result.message))
