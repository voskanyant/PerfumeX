from django.core.management.base import BaseCommand

from assistant_linking.services.normalization_stats import refresh_stats_snapshot


class Command(BaseCommand):
    help = "Refresh saved normalisation dashboard stats."

    def handle(self, *args, **options):
        snapshot = refresh_stats_snapshot(hidden_keywords=[])
        self.stdout.write(
            self.style.SUCCESS(
                f"Refreshed normalisation stats for {snapshot.parser_version} "
                f"({snapshot.scope_key}) at {snapshot.generated_at}."
            )
        )
