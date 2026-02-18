from django.core.management.base import BaseCommand
from django.utils import timezone

from prices import models
from prices.services.email_importer import run_import
from prices.services.cbr_rates import upsert_cbr_markup_rates


class Command(BaseCommand):
    help = "Import supplier price lists from email attachments."

    def add_arguments(self, parser):
        parser.add_argument("--mailbox", help="Mailbox name to process.")
        parser.add_argument("--all", action="store_true", help="Process all messages.")
        parser.add_argument("--limit", type=int, default=0, help="Limit messages.")
        parser.add_argument(
            "--mark-seen",
            action="store_true",
            help="Mark processed messages as seen.",
        )
        parser.add_argument(
            "--max-bytes",
            type=int,
            default=20_000_000,
            help="Skip emails larger than this size (bytes).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run immediately, ignoring Import Settings interval/disabled flag.",
        )

    def handle(self, *args, **options):
        settings_obj = models.ImportSettings.get_solo()
        if not options["force"]:
            if not settings_obj.enabled:
                self.stdout.write("Import settings disabled. Use --force to run.")
                return
            if settings_obj.last_run_at:
                elapsed = timezone.now() - settings_obj.last_run_at
                if elapsed.total_seconds() < settings_obj.interval_minutes * 60:
                    self.stdout.write("Skipped. Last run too recent.")
                    return

        mailboxes = models.Mailbox.objects.filter(is_active=True)
        if options["mailbox"]:
            mailboxes = mailboxes.filter(name=options["mailbox"])

        today = timezone.localdate()
        cbr_rate_exists_today = models.ExchangeRate.objects.filter(
            rate_date=today,
            from_currency=models.Currency.USD,
            to_currency=models.Currency.RUB,
            source__startswith="CBR + ",
        ).exists()
        if cbr_rate_exists_today:
            self.stdout.write("CBR daily rate already synced for today.")
        else:
            try:
                upsert_cbr_markup_rates(
                    today,
                    settings_obj.cbr_markup_percent,
                )
                self.stdout.write("CBR daily USD->RUB rate synced.")
            except Exception as exc:
                self.stdout.write(f"CBR rate sync skipped: {exc}")

        run_import(
            mailboxes=mailboxes,
            supplier_id=None,
            mark_seen=options["mark_seen"],
            limit=options["limit"],
            max_bytes=options["max_bytes"],
            logger=self.stdout.write,
            dedupe_same_day_only=False,
        )

        settings_obj.last_run_at = timezone.now()
        settings_obj.save(update_fields=["last_run_at"])
