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
    if not _has_active_worker_for_queue(rq_module, connection, queue):
        raise RuntimeError(
            f"No active RQ worker is registered for queue '{queue}'. "
            "Start `python manage.py run_rq_worker` before queueing background jobs."
        )
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


def _has_active_worker_for_queue(rq_module, connection, queue_name: str) -> bool:
    worker_model = getattr(rq_module, "Worker", None)
    if worker_model is None or not hasattr(worker_model, "all"):
        return True
    workers = worker_model.all(connection=connection)
    for worker in workers:
        queue_names = _worker_queue_names(worker)
        if queue_name in queue_names:
            return True
    return False


def _worker_queue_names(worker) -> set[str]:
    queue_names = getattr(worker, "queue_names", None)
    if callable(queue_names):
        return {str(name) for name in queue_names()}
    if queue_names:
        return {str(name) for name in queue_names}
    queues = getattr(worker, "queues", None) or []
    return {
        str(getattr(queue, "name", queue))
        for queue in queues
        if getattr(queue, "name", queue)
    }


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
