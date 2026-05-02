from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from django.db.models import F
from django.urls import reverse_lazy
from django.utils import timezone

from prices import models
from prices.services.email_import_lock import email_import_worker_is_busy
from prices.services.job_queue import enqueue_management_command


EMAIL_IMPORT_BUSY_MESSAGE = (
    "Another email import is already running. Wait for it to finish or cancel it first."
)


@dataclass(frozen=True)
class ImportDateRangeParse:
    start_date: date | None
    end_date: date | None
    error_message: str

    @property
    def is_valid(self) -> bool:
        return not self.error_message


@dataclass(frozen=True)
class StuckEmailImportRuns:
    cutoff: object
    runs: object


@dataclass(frozen=True)
class EmailImportActionResult:
    message_level: str
    message: str
    updated: int = 0


def parse_import_date_range(
    *,
    start_raw: str,
    end_raw: str,
    require_start: bool = False,
    missing_start_message: str = "Start date is required.",
    validate_order: bool = False,
) -> ImportDateRangeParse:
    start_text = (start_raw or "").strip()
    end_text = (end_raw or "").strip()
    if require_start and not start_text:
        return ImportDateRangeParse(None, None, missing_start_message)

    start_date = None
    if start_text:
        try:
            start_date = datetime.fromisoformat(start_text).date()
        except ValueError:
            return ImportDateRangeParse(None, None, "Start date is invalid.")

    end_date = None
    if end_text:
        try:
            end_date = datetime.fromisoformat(end_text).date()
        except ValueError:
            return ImportDateRangeParse(start_date, None, "End date is invalid.")

    if validate_order and start_date and end_date and end_date < start_date:
        return ImportDateRangeParse(
            start_date,
            end_date,
            "End date must be on or after start date.",
        )

    return ImportDateRangeParse(start_date, end_date, "")


def stuck_email_import_cutoff(*, now=None, minutes: int = 30):
    return (now or timezone.now()) - timezone.timedelta(minutes=minutes)


def stuck_email_import_runs(
    *,
    cutoff=None,
    minutes: int = 30,
    run_manager=None,
) -> StuckEmailImportRuns:
    cutoff = cutoff or stuck_email_import_cutoff(minutes=minutes)
    run_manager = run_manager or models.EmailImportRun.objects
    runs = (
        run_manager.select_related("supplier")
        .filter(status=models.EmailImportStatus.RUNNING, updated_at__lt=cutoff)
        .order_by("updated_at", "started_at", "id")
    )
    return StuckEmailImportRuns(cutoff=cutoff, runs=runs)


def build_stuck_email_import_runs_context(
    *,
    stuck_runs_func=stuck_email_import_runs,
) -> dict:
    stuck_runs = stuck_runs_func()
    return {
        "stuck_runs": stuck_runs.runs,
        "cutoff": stuck_runs.cutoff,
        "import_section": "stuck_runs",
        "detailed_logs_url": reverse_lazy("prices:import_detailed_logs"),
        "overview_url": reverse_lazy("prices:supplier_overview"),
    }


def latest_email_import_run_for_supplier(supplier_id, *, run_manager=None):
    run_manager = run_manager or models.EmailImportRun.objects
    return run_manager.filter(supplier_id=supplier_id).order_by("-started_at").first()


def build_email_import_status_payload(run) -> dict:
    if not run:
        return {"status": "idle"}
    progress = None
    if run.total_messages:
        progress = int((run.processed_messages / run.total_messages) * 100)
    detailed_log_tail = (run.detailed_log or "")[-8000:]
    return {
        "status": run.status,
        "progress": progress,
        "processed_files": run.processed_files,
        "errors": run.errors,
        "last_message": run.last_message,
        "detailed_log": detailed_log_tail,
    }


def supplier_email_import_status_payload(
    supplier_id,
    *,
    run_manager=None,
    expire_func=None,
) -> dict:
    expire_func = expire_func or expire_stale_email_import_runs
    expire_func()
    run = latest_email_import_run_for_supplier(
        supplier_id,
        run_manager=run_manager,
    )
    return build_email_import_status_payload(run)


def build_process_email_runs_command_args(
    run_ids,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[str]:
    command_args = ["process_email_runs"]
    for run_id in run_ids:
        command_args.extend(["--run-id", str(run_id)])
    if start_date:
        command_args.extend(["--start-date", start_date.isoformat()])
    if end_date:
        command_args.extend(["--end-date", end_date.isoformat()])
    return command_args


def enqueue_process_email_runs(
    run_ids,
    *,
    description: str,
    start_date: date | None = None,
    end_date: date | None = None,
    enqueue_func=enqueue_management_command,
):
    command_args = build_process_email_runs_command_args(
        run_ids,
        start_date=start_date,
        end_date=end_date,
    )
    return enqueue_func(command_args[0], *command_args[1:], description=description)


def enqueue_forced_email_import_scan(
    *,
    description: str,
    enqueue_func=enqueue_management_command,
):
    return enqueue_func("import_emails", "--force", description=description)


def run_manual_email_import_action(
    *,
    description: str = "Manual email import",
    has_running_func=None,
    enqueue_func=enqueue_forced_email_import_scan,
) -> EmailImportActionResult:
    has_running = has_running_func or has_running_email_imports
    if has_running():
        return EmailImportActionResult("info", EMAIL_IMPORT_BUSY_MESSAGE)
    try:
        enqueue_func(description=description)
    except Exception as exc:
        return EmailImportActionResult(
            "error",
            f"Failed to start email import: {exc}",
        )
    return EmailImportActionResult("success", "Email import started.")


def run_supplier_board_mailbox_scan_action(
    *,
    has_running_func=None,
    enqueue_func=enqueue_forced_email_import_scan,
) -> EmailImportActionResult:
    has_running = has_running_func or has_running_email_imports
    if has_running():
        return EmailImportActionResult("info", EMAIL_IMPORT_BUSY_MESSAGE)
    try:
        enqueue_func(description="Supplier board mailbox scan")
    except Exception as exc:
        return EmailImportActionResult(
            "error",
            f"Failed to start mailbox scan: {exc}",
        )
    return EmailImportActionResult("info", "Mailbox scan started.")


def active_email_backfill_suppliers(supplier_ids, *, supplier_manager=None) -> list:
    supplier_manager = supplier_manager or models.Supplier.objects
    return list(supplier_manager.filter(id__in=supplier_ids, is_active=True))


def build_email_backfill_run_message(
    label: str,
    *,
    start_date: date,
    end_date: date | None = None,
) -> str:
    return f"{label} {start_date.isoformat()} to {end_date or 'today'}"


def create_email_import_run(
    supplier,
    *,
    last_message: str | None = None,
):
    create_kwargs = {
        "supplier": supplier,
        "status": models.EmailImportStatus.RUNNING,
    }
    if last_message is not None:
        create_kwargs["last_message"] = last_message
    return models.EmailImportRun.objects.create(**create_kwargs)


def run_supplier_email_import_action(
    supplier,
    *,
    has_running_func=None,
    create_run_func=create_email_import_run,
    enqueue_func=enqueue_process_email_runs,
    mark_failed_func=None,
) -> EmailImportActionResult:
    if not supplier.from_address_pattern:
        return EmailImportActionResult(
            "info",
            "Supplier has no sender email configured. Set From address pattern first.",
        )
    has_running = has_running_func or has_running_email_imports
    if has_running():
        return EmailImportActionResult("info", EMAIL_IMPORT_BUSY_MESSAGE)
    mark_failed_func = mark_failed_func or mark_email_import_runs_failed
    run = create_run_func(supplier)
    try:
        enqueue_func(
            [run.id],
            description=f"Email import for {supplier.name}",
        )
    except Exception as exc:
        mark_failed_func(
            [run.id],
            last_message=f"Failed to start background import: {exc}",
        )
        return EmailImportActionResult(
            "error",
            f"Failed to start email import: {exc}",
        )
    return EmailImportActionResult("", "", updated=1)


def run_supplier_email_backfill_action(
    supplier,
    *,
    start_raw: str = "",
    end_raw: str = "",
    has_running_func=None,
    create_run_func=create_email_import_run,
    enqueue_func=enqueue_process_email_runs,
    mark_failed_func=None,
) -> EmailImportActionResult:
    if not supplier.from_address_pattern:
        return EmailImportActionResult(
            "info",
            "Supplier has no sender email configured. Set From address pattern first.",
        )
    has_running = has_running_func or has_running_email_imports
    if has_running():
        return EmailImportActionResult("info", EMAIL_IMPORT_BUSY_MESSAGE)

    date_range = parse_import_date_range(
        start_raw=start_raw,
        end_raw=end_raw,
        require_start=True,
        missing_start_message="Start date is required for backfill.",
    )
    if not date_range.is_valid:
        return EmailImportActionResult("info", date_range.error_message)

    mark_failed_func = mark_failed_func or mark_email_import_runs_failed
    run = create_run_func(
        supplier=supplier,
        last_message=build_email_backfill_run_message(
            "Backfill",
            start_date=date_range.start_date,
            end_date=date_range.end_date,
        ),
    )
    try:
        enqueue_func(
            [run.id],
            description=f"Email backfill for {supplier.name}",
            start_date=date_range.start_date,
            end_date=date_range.end_date,
        )
    except Exception as exc:
        mark_failed_func(
            [run.id],
            last_message=f"Failed to start backfill: {exc}",
        )
        return EmailImportActionResult("error", f"Failed to start backfill: {exc}")
    return EmailImportActionResult("", "", updated=1)


def run_bulk_email_backfill_action(
    supplier_ids,
    *,
    start_raw: str = "",
    end_raw: str = "",
    has_running_func=None,
    active_suppliers_func=None,
    create_runs_func=None,
    enqueue_func=enqueue_process_email_runs,
    mark_failed_func=None,
) -> EmailImportActionResult:
    has_running = has_running_func or has_running_email_imports
    if has_running():
        return EmailImportActionResult("info", EMAIL_IMPORT_BUSY_MESSAGE)

    supplier_ids = list(supplier_ids)
    if not supplier_ids:
        return EmailImportActionResult(
            "info",
            "Select at least one supplier for backfill.",
        )

    date_range = parse_import_date_range(
        start_raw=start_raw,
        end_raw=end_raw,
        require_start=True,
        missing_start_message="Start date is required for bulk backfill.",
    )
    if not date_range.is_valid:
        return EmailImportActionResult("info", date_range.error_message)

    active_suppliers_func = active_suppliers_func or active_email_backfill_suppliers
    suppliers = active_suppliers_func(supplier_ids)
    if not suppliers:
        return EmailImportActionResult("info", "No valid suppliers selected.")

    create_runs_func = create_runs_func or create_email_backfill_runs_for_suppliers
    run_ids = create_runs_func(
        suppliers,
        start_date=date_range.start_date,
        end_date=date_range.end_date,
    )
    if not run_ids:
        return EmailImportActionResult(
            "info",
            "No selected suppliers have sender email configured.",
        )

    mark_failed_func = mark_failed_func or mark_email_import_runs_failed
    try:
        enqueue_func(
            run_ids,
            description="Bulk supplier email backfill",
            start_date=date_range.start_date,
            end_date=date_range.end_date,
        )
    except Exception as exc:
        mark_failed_func(
            run_ids,
            last_message=f"Failed to start bulk backfill: {exc}",
        )
        return EmailImportActionResult(
            "error",
            f"Failed to start bulk backfill: {exc}",
        )
    return EmailImportActionResult(
        "success",
        f"Backfill queued for {len(run_ids)} supplier(s).",
        updated=len(run_ids),
    )


def create_email_backfill_runs_for_suppliers(
    suppliers,
    *,
    start_date: date,
    end_date: date | None = None,
    label: str = "Bulk backfill",
    create_run_func=create_email_import_run,
) -> list[int]:
    run_ids = []
    last_message = build_email_backfill_run_message(
        label,
        start_date=start_date,
        end_date=end_date,
    )
    for supplier in suppliers:
        if not supplier.from_address_pattern:
            continue
        run = create_run_func(supplier=supplier, last_message=last_message)
        run_ids.append(run.id)
    return run_ids


def mark_email_import_runs_failed(
    run_ids,
    *,
    last_message: str,
    finished_at=None,
) -> int:
    run_ids = list(run_ids)
    if not run_ids:
        return 0
    return models.EmailImportRun.objects.filter(id__in=run_ids).update(
        status=models.EmailImportStatus.FAILED,
        finished_at=finished_at or timezone.now(),
        errors=1,
        last_message=last_message,
    )


def mark_email_import_run_failed_from_recovery(
    run_id: int,
    *,
    finished_at=None,
) -> int:
    return models.EmailImportRun.objects.filter(
        id=run_id,
        status=models.EmailImportStatus.RUNNING,
    ).update(
        status=models.EmailImportStatus.FAILED,
        finished_at=finished_at or timezone.now(),
        errors=F("errors") + 1,
        last_message="Marked failed from stuck-run recovery.",
    )


def recover_stuck_email_import_run(
    run_id_raw,
    *,
    mark_func=mark_email_import_run_failed_from_recovery,
) -> EmailImportActionResult:
    run_id_text = str(run_id_raw or "").strip()
    if not run_id_text.isdigit():
        return EmailImportActionResult("error", "Select a valid import run.")
    updated = mark_func(int(run_id_text))
    if updated:
        return EmailImportActionResult(
            "success",
            "Import run marked as failed.",
            updated=updated,
        )
    return EmailImportActionResult(
        "info",
        "Import run is no longer running.",
        updated=0,
    )


def cancel_running_email_imports_for_supplier(
    supplier,
    *,
    finished_at=None,
) -> int:
    return models.EmailImportRun.objects.filter(
        supplier=supplier,
        status=models.EmailImportStatus.RUNNING,
    ).update(
        status=models.EmailImportStatus.CANCELED,
        finished_at=finished_at or timezone.now(),
        last_message="Canceled by user.",
    )


def cancel_supplier_email_import(
    supplier,
    *,
    expire_func=None,
    cancel_func=cancel_running_email_imports_for_supplier,
) -> EmailImportActionResult:
    expire_func = expire_func or expire_stale_email_import_runs
    expire_func()
    updated = cancel_func(supplier)
    if updated:
        return EmailImportActionResult(
            "info",
            "Email import marked as canceled.",
            updated=updated,
        )
    return EmailImportActionResult("info", "No running import to cancel.", updated=0)


def email_import_timeout_seconds() -> int | None:
    timeout_minutes = int(
        models.ImportSettings.get_solo().supplier_timeout_minutes or 0
    )
    return timeout_minutes * 60 if timeout_minutes > 0 else None


def expire_stale_email_import_runs() -> int:
    timeout_seconds = email_import_timeout_seconds()
    if not timeout_seconds:
        return 0
    cutoff = timezone.now() - timezone.timedelta(seconds=timeout_seconds)
    return models.EmailImportRun.objects.filter(
        status=models.EmailImportStatus.RUNNING,
        started_at__lt=cutoff,
    ).update(
        status=models.EmailImportStatus.FAILED,
        finished_at=timezone.now(),
        errors=F("errors") + 1,
        last_message="Auto-failed timeout. Previous run exceeded supplier timeout.",
    )


def has_running_email_imports(
    supplier=None,
    worker_busy_func: Callable[[], bool] | None = None,
) -> bool:
    expire_stale_email_import_runs()
    is_worker_busy = worker_busy_func or email_import_worker_is_busy
    if is_worker_busy():
        return True
    runs = models.EmailImportRun.objects.filter(status=models.EmailImportStatus.RUNNING)
    if supplier is not None:
        runs = runs.filter(supplier=supplier)
    return runs.exists()
