from django.core.management.base import BaseCommand

from assistant_linking.services.normalization_stats import refresh_stats_snapshot, snapshot_scope_key
from prices.models import UserPreference
from prices.services.product_visibility import parse_hidden_product_keywords


class Command(BaseCommand):
    help = "Refresh saved normalisation dashboard stats."

    def add_arguments(self, parser):
        parser.add_argument("--all-user-scopes", action="store_true")

    def handle(self, *args, **options):
        scopes = [[]]
        if options["all_user_scopes"]:
            seen = {snapshot_scope_key([])}
            for raw_terms in UserPreference.objects.values_list("supplier_exclude_terms", flat=True).iterator():
                hidden_keywords = parse_hidden_product_keywords(raw_terms or "")
                scope_key = snapshot_scope_key(hidden_keywords)
                if scope_key in seen:
                    continue
                seen.add(scope_key)
                scopes.append(hidden_keywords)

        for hidden_keywords in scopes:
            snapshot = refresh_stats_snapshot(hidden_keywords=hidden_keywords)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Refreshed normalisation stats for {snapshot.parser_version} "
                    f"({snapshot.scope_key}) at {snapshot.generated_at}."
                )
            )
