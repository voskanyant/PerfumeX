from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from prices import models
from prices.services.cbr_rates import (
    upsert_cbr_markup_rates,
    upsert_cbr_markup_rates_range,
)


class Command(BaseCommand):
    help = "Sync CBR USD/RUB rates using the configured markup."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, required=False)
        parser.add_argument("--start-date", type=str, required=False)
        parser.add_argument("--end-date", type=str, required=False)
        parser.add_argument("--markup-percent", type=str, required=False)

    def handle(self, *args, **options):
        target_date = options.get("date")
        start_date = options.get("start_date")
        end_date = options.get("end_date")
        markup_percent = self._markup_percent(options.get("markup_percent"))

        if target_date and (start_date or end_date):
            raise CommandError("Use either --date or --start-date/--end-date, not both.")
        if not target_date and not start_date:
            raise CommandError("Provide --date or --start-date.")

        if target_date:
            rate_date = self._parse_date(target_date, "--date")
            rate = upsert_cbr_markup_rates(rate_date, markup_percent)
            self.stdout.write(f"Synced CBR rate for {rate_date}: USD->RUB {rate}")
            return

        start = self._parse_date(start_date, "--start-date")
        end = self._parse_date(end_date, "--end-date") if end_date else start
        if end < start:
            raise CommandError("--end-date must be on or after --start-date.")

        result = upsert_cbr_markup_rates_range(
            start_date=start,
            end_date=end,
            markup_percent=markup_percent,
        )
        self.stdout.write(
            "CBR range synced: "
            f"{result['synced_days']}/{result['total_days']} day(s), "
            f"errors={len(result['errors'])}."
        )
        for error in result["errors"]:
            self.stdout.write(f"ERROR {error}")

    def _markup_percent(self, raw_value: str | None) -> Decimal:
        if raw_value in {None, ""}:
            return models.ImportSettings.get_solo().cbr_markup_percent
        try:
            return Decimal(raw_value)
        except (InvalidOperation, TypeError) as exc:
            raise CommandError("--markup-percent must be a decimal value.") from exc

    def _parse_date(self, raw_value: str | None, option_name: str) -> date:
        if not raw_value:
            raise CommandError(f"{option_name} is required.")
        try:
            return date.fromisoformat(raw_value)
        except ValueError as exc:
            raise CommandError(f"{option_name} must use YYYY-MM-DD format.") from exc
