from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run an RQ worker for PerfumeX background jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "queues",
            nargs="*",
            help="Queue names to process. Defaults to RQ_DEFAULT_QUEUE.",
        )
        parser.add_argument(
            "--burst",
            action="store_true",
            help="Stop when all queued jobs have been processed.",
        )

    def handle(self, *args, **options):
        try:
            import redis
            import rq
        except ImportError as exc:
            raise CommandError(
                "Install redis and rq from requirements.txt before running the worker."
            ) from exc

        queue_names = options["queues"] or [settings.RQ_DEFAULT_QUEUE]
        connection = redis.Redis.from_url(settings.REDIS_URL)
        queues = [rq.Queue(name, connection=connection) for name in queue_names]
        worker = rq.Worker(queues, connection=connection)
        self.stdout.write(
            f"Starting RQ worker for queues: {', '.join(queue_names)} "
            f"({settings.REDIS_URL})"
        )
        worker.work(burst=options["burst"])
