from django.core.management.base import BaseCommand

from prices import models
from prices.services.email_importer import run_import


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

    def handle(self, *args, **options):
        mailboxes = models.Mailbox.objects.filter(is_active=True)
        if options["mailbox"]:
            mailboxes = mailboxes.filter(name=options["mailbox"])

        run_import(
            mailboxes=mailboxes,
            supplier_id=None,
            mark_seen=options["mark_seen"],
            limit=options["limit"],
            max_bytes=options["max_bytes"],
            logger=self.stdout.write,
            dedupe_same_day_only=False,
        )
