from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections


@dataclass(frozen=True)
class JobDispatchResult:
    job_id: str
    queue_name: str
    status: str
    queued: bool
    description: str


def _run_management_command(
    command_name: str,
    args: tuple[Any, ...],
    options: dict[str, Any],
) -> None:
    close_old_connections()
    try:
        call_command(command_name, *args, **options)
    finally:
        close_old_connections()


def enqueue_management_command(
    command_name: str,
    *args: Any,
    queue_name: str | None = None,
    description: str = "",
    **options: Any,
) -> JobDispatchResult:
    """Run or enqueue a management command through the PerfumeX job queue.

    In DEBUG/local tests this can run synchronously via PERFUMEX_RQ_SYNC. In
    production it enqueues to RQ and returns the queued job id.
    """

    queue = queue_name or settings.RQ_DEFAULT_QUEUE
    label = description or f"manage.py {command_name}"

    if settings.PERFUMEX_RQ_SYNC:
        _run_management_command(command_name, tuple(args), dict(options))
        return JobDispatchResult(
            job_id="",
            queue_name=queue,
            status="finished",
            queued=False,
            description=label,
        )

    redis_module, rq_module = _import_rq()
    connection = redis_module.Redis.from_url(settings.REDIS_URL)
    rq_queue = rq_module.Queue(queue, connection=connection)
    job = rq_queue.enqueue(
        _run_management_command,
        command_name,
        tuple(args),
        dict(options),
        job_timeout=settings.RQ_JOB_TIMEOUT_SECONDS,
        description=label,
    )
    return JobDispatchResult(
        job_id=job.id,
        queue_name=queue,
        status="queued",
        queued=True,
        description=label,
    )


def _import_rq():
    try:
        import redis
        import rq
    except ImportError as exc:
        raise RuntimeError(
            "RQ job dispatch requires redis and rq. Install requirements.txt or set "
            "PERFUMEX_RQ_SYNC=1 for local synchronous execution."
        ) from exc
    return redis, rq
