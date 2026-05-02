from __future__ import annotations

from urllib.parse import urlencode

from django.db.models import F, Max, Window
from django.db.models.functions import Coalesce, RowNumber
from django.urls import reverse_lazy
from django.utils import timezone

from prices import models


PRODUCT_REMOVED_EVENT_PREFIX = "SYSTEM_DEACTIVATE:"

SUPPLIER_EMAIL_STATUS_ROW_KEYS = (
    "is_running",
    "has_email_route",
    "last_import_relative",
    "last_import_full",
    "last_import_age_class",
    "last_import_note",
    "check_label",
    "check_class",
    "check_code",
    "check_note",
    "check_relative",
    "check_full",
    "check_progress",
    "check_has_time",
    "health_label",
    "health_class",
    "health_code",
    "health_note",
    "expected_at",
    "file_summary",
    "problem_note",
    "latest_reason_code",
    "source_mailbox_folder",
)


def collect_latest_successful_imports() -> dict[int, models.ImportBatch]:
    batches = (
        models.ImportBatch.objects.select_related("supplier", "mailbox")
        .filter(
            status=models.ImportStatus.PROCESSED,
            importfile__file_kind=models.FileKind.PRICE,
        )
        .exclude(message_id__startswith=PRODUCT_REMOVED_EVENT_PREFIX)
        .annotate(updated_at=Coalesce(Max("importfile__processed_at"), "created_at"))
        .annotate(
            supplier_rank=Window(
                expression=RowNumber(),
                partition_by=[F("supplier_id")],
                order_by=[
                    F("updated_at").desc(nulls_last=True),
                    F("received_at").desc(nulls_last=True),
                    F("created_at").desc(nulls_last=True),
                    F("id").desc(),
                ],
            )
        )
        .filter(supplier_rank=1)
    )
    return {batch.supplier_id: batch for batch in batches}


def collect_latest_failed_import_files() -> dict[int, models.ImportFile]:
    files = (
        models.ImportFile.objects.select_related(
            "import_batch", "import_batch__supplier"
        )
        .filter(status=models.ImportStatus.FAILED)
        .annotate(
            supplier_rank=Window(
                expression=RowNumber(),
                partition_by=[F("import_batch__supplier_id")],
                order_by=[
                    F("import_batch__created_at").desc(nulls_last=True),
                    F("id").desc(),
                ],
            )
        )
        .filter(supplier_rank=1)
    )
    return {import_file.import_batch.supplier_id: import_file for import_file in files}


def collect_latest_attachment_diagnostics() -> (
    dict[int, models.EmailAttachmentDiagnostic]
):
    diagnostics = (
        models.EmailAttachmentDiagnostic.objects.select_related("supplier")
        .filter(supplier__isnull=False)
        .annotate(
            supplier_rank=Window(
                expression=RowNumber(),
                partition_by=[F("supplier_id")],
                order_by=[F("created_at").desc(nulls_last=True), F("id").desc()],
            )
        )
        .filter(supplier_rank=1)
    )
    return {diagnostic.supplier_id: diagnostic for diagnostic in diagnostics}


def collect_active_price_mappings() -> dict[int, models.SupplierFileMapping]:
    mappings = (
        models.SupplierFileMapping.objects.filter(
            file_kind=models.FileKind.PRICE,
            is_active=True,
        )
        .select_related("supplier")
        .order_by("supplier_id", "-id")
    )
    latest: dict[int, models.SupplierFileMapping] = {}
    for mapping in mappings:
        if mapping.supplier_id not in latest:
            latest[mapping.supplier_id] = mapping
    return latest


def collect_latest_runs_and_streaks() -> (
    tuple[dict[int, models.EmailImportRun], dict[int, int]]
):
    base_runs = models.EmailImportRun.objects.select_related("supplier")
    latest_runs = {
        run.supplier_id: run
        for run in base_runs.annotate(
            supplier_rank=Window(
                expression=RowNumber(),
                partition_by=[F("supplier_id")],
                order_by=[F("started_at").desc(nulls_last=True), F("id").desc()],
            )
        ).filter(supplier_rank=1)
    }
    streaks: dict[int, int] = {supplier_id: 1 for supplier_id in latest_runs}
    target_codes: dict[int, str] = {
        supplier_id: str(build_email_run_status(run).get("code") or "unknown")
        for supplier_id, run in latest_runs.items()
    }
    recent_runs = list(base_runs.order_by("-started_at", "-id")[:1000])
    seen_latest: set[int] = set()
    closed: set[int] = set()
    for run in recent_runs:
        supplier_id = run.supplier_id
        if supplier_id not in latest_runs or supplier_id in closed:
            continue
        code = str(build_email_run_status(run).get("code") or "unknown")
        if supplier_id not in seen_latest:
            if run.id == latest_runs[supplier_id].id:
                seen_latest.add(supplier_id)
            continue
        if target_codes.get(supplier_id) == code:
            streaks[supplier_id] += 1
        else:
            closed.add(supplier_id)
    return latest_runs, streaks


def diagnostic_activity_datetime(diagnostic):
    if not diagnostic:
        return None
    return diagnostic.message_date or diagnostic.created_at


def diagnostic_check_datetime(diagnostic):
    if not diagnostic:
        return None
    return diagnostic.created_at


def latest_global_mailbox_check_datetime():
    mailbox_dt = models.Mailbox.objects.filter(is_active=True).aggregate(
        latest=Max("last_checked_at")
    )["latest"]
    folder_dt = models.MailboxFolderCursor.objects.filter(
        mailbox__is_active=True
    ).aggregate(latest=Max("last_checked_at"))["latest"]
    settings_dt = models.ImportSettings.get_solo().last_run_at
    return max([dt for dt in (mailbox_dt, folder_dt, settings_dt) if dt], default=None)


def supplier_email_check_datetime(supplier, event_dt=None):
    candidates = [supplier.last_email_check_at, event_dt]
    if supplier.from_address_pattern:
        candidates.append(latest_global_mailbox_check_datetime())
    return max([dt for dt in candidates if dt], default=None)


def failed_file_activity_datetime(import_file):
    if not import_file:
        return None
    batch = getattr(import_file, "import_batch", None)
    return (
        import_file.processed_at
        or (getattr(batch, "received_at", None) if batch else None)
        or (getattr(batch, "created_at", None) if batch else None)
    )


def format_event_filename(filename: str, limit: int = 48) -> str:
    filename = (filename or "").strip()
    if not filename:
        return ""
    if len(filename) <= limit:
        return filename
    return f"{filename[: limit - 3]}..."


def short_relative_datetime(value) -> str:
    if not value:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    now = timezone.localtime(timezone.now())
    dt_local = timezone.localtime(dt)
    total_seconds = int((now - dt_local).total_seconds())
    if total_seconds <= 0:
        return "just now"
    if total_seconds < 60:
        return "just now"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m ago"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h ago"
    if total_seconds < 604800:
        return f"{total_seconds // 86400}d ago"
    if total_seconds < 2592000:
        return f"{total_seconds // 604800}w ago"
    if total_seconds < 31536000:
        return f"{total_seconds // 2592000}mo ago"
    return f"{total_seconds // 31536000}y ago"


def imported_age_class(value) -> str:
    if not value:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    now = timezone.localtime(timezone.now())
    dt_local = timezone.localtime(dt)
    age_seconds = max(int((now - dt_local).total_seconds()), 0)
    if age_seconds < 3 * 24 * 60 * 60:
        return "age-fresh"
    if age_seconds <= 5 * 24 * 60 * 60:
        return "age-warn"
    return "age-stale"


def format_local_datetime(value) -> str:
    if not value:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")


def batch_activity_datetime(batch):
    if not batch:
        return None
    dt = getattr(batch, "updated_at", None) or batch.created_at or batch.received_at
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def build_last_import_info(batch) -> dict[str, str | int]:
    batch_dt = batch_activity_datetime(batch)
    if not batch or not batch_dt:
        return {
            "relative": "Never",
            "full": "",
            "class_name": "age-stale",
            "note": "No successful import yet",
            "source_code": "never",
            "sort_age_seconds": 10**12,
            "datetime": None,
        }
    if batch.mailbox_id:
        mailbox_name = getattr(batch.mailbox, "name", "") or "mailbox"
        folder = (getattr(batch, "message_folder", "") or "").strip()
        note = f"{mailbox_name} / {folder}" if folder else f"{mailbox_name} email"
        source_code = "email"
    else:
        note = "Manual upload / backfill"
        source_code = "manual"
    now = timezone.localtime(timezone.now())
    age_seconds = max(int((now - batch_dt).total_seconds()), 0)
    return {
        "relative": short_relative_datetime(batch_dt),
        "full": format_local_datetime(batch_dt),
        "class_name": imported_age_class(batch_dt),
        "note": note,
        "source_code": source_code,
        "sort_age_seconds": age_seconds,
        "datetime": batch_dt,
    }


def run_activity_datetime(run):
    if not run:
        return None
    dt = run.finished_at or run.started_at
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def expected_import_interval_hours(supplier) -> int:
    value = int(getattr(supplier, "expected_import_interval_hours", 0) or 0)
    return value if value > 0 else 24


def format_interval_hours(hours: int) -> str:
    if hours % 24 == 0:
        days = hours // 24
        return f"{days}d" if days != 1 else "1d"
    return f"{hours}h"


def format_expected_cadence(supplier) -> str:
    hours = expected_import_interval_hours(supplier)
    if hours == 24:
        return "daily, weekdays"
    return f"every {format_interval_hours(hours)}, weekdays"


def add_business_interval(start, hours: int):
    if not start:
        return None
    current = timezone.localtime(start)
    safe_hours = max(int(hours or 24), 1)
    if safe_hours % 24 == 0:
        business_days = max(safe_hours // 24, 1)
        added = 0
        while added < business_days:
            current = current + timezone.timedelta(days=1)
            if current.weekday() < 5:
                added += 1
        return current
    current = current + timezone.timedelta(hours=safe_hours)
    while current.weekday() >= 5:
        current = current + timezone.timedelta(days=1)
    return current


def format_expected_deadline(value) -> str:
    if not value:
        return ""
    return timezone.localtime(value).strftime("%a %d/%m %H:%M")


def attachment_reason_label(reason_code: str, decision: str = "") -> str:
    reason_labels = dict(models.AttachmentReason.choices)
    decision_labels = dict(models.AttachmentDecision.choices)
    return (
        reason_labels.get(reason_code)
        or decision_labels.get(decision)
        or "Attachment decision"
    )


def build_latest_check_info(
    supplier,
    run,
    streak_count: int = 1,
    latest_diagnostic=None,
) -> dict[str, str | int | None | bool]:
    diagnostic_dt = diagnostic_check_datetime(latest_diagnostic)
    fallback_dt = supplier_email_check_datetime(supplier, diagnostic_dt)
    if run:
        run_dt = run_activity_datetime(run)
        newest_side_event_dt = max(
            [dt for dt in (fallback_dt, diagnostic_dt) if dt],
            default=None,
        )
        if newest_side_event_dt:
            compare_dt = newest_side_event_dt
            if timezone.is_naive(compare_dt):
                compare_dt = timezone.make_aware(
                    compare_dt, timezone.get_current_timezone()
                )
            if not run_dt or compare_dt > run_dt:
                if diagnostic_dt and compare_dt == diagnostic_dt:
                    return build_diagnostic_event_check(latest_diagnostic)
                return build_supplier_email_fallback_check(supplier, fallback_dt)
        check_dt = max([dt for dt in (fallback_dt, run_dt) if dt], default=None)
        run_status = build_email_run_status(run)
        note = str(run_status.get("note") or "")
        code = str(run_status.get("code") or "unknown")
        if code == "failed" and streak_count > 1:
            note = f"{streak_count} failed checks in a row"
        elif code == "no-files" and streak_count > 1:
            note = f"{streak_count} no-file checks in a row"
        elif code == "no-change" and streak_count > 1:
            note = f"{streak_count} unchanged checks in a row"
        return {
            "label": str(run_status["label"]),
            "class_name": str(run_status["class_name"]),
            "code": code,
            "note": note,
            "relative": short_relative_datetime(check_dt) if check_dt else "Checked",
            "full": format_local_datetime(check_dt),
            "progress": run_status.get("progress"),
            "show_time": bool(check_dt),
        }

    if diagnostic_dt and (not fallback_dt or diagnostic_dt >= fallback_dt):
        return build_diagnostic_event_check(latest_diagnostic)

    if fallback_dt:
        return build_supplier_email_fallback_check(supplier, fallback_dt)

    if not supplier.from_address_pattern:
        return {
            "label": "not configured",
            "class_name": "is-missing",
            "code": "not-configured",
            "note": "Supplier email route missing",
            "relative": "Not configured",
            "full": "",
            "progress": None,
            "show_time": False,
        }

    return {
        "label": "idle",
        "class_name": "is-neutral",
        "code": "idle",
        "note": "No email check recorded yet",
        "relative": "Not checked",
        "full": "",
        "progress": None,
        "show_time": False,
    }


def build_supplier_email_fallback_check(
    supplier, fallback_dt
) -> dict[str, str | int | None | bool]:
    return {
        "label": "current",
        "class_name": "is-neutral",
        "code": "successful" if supplier.last_email_processed else "no-change",
        "note": "",
        "relative": short_relative_datetime(fallback_dt),
        "full": format_local_datetime(fallback_dt),
        "progress": None,
        "show_time": True,
    }


def build_diagnostic_event_check(diagnostic) -> dict[str, str | int | None | bool]:
    supplier = diagnostic.supplier
    decision = diagnostic.decision
    filename = format_event_filename(diagnostic.filename)
    reason = attachment_reason_label(diagnostic.reason_code, decision)
    if decision == models.AttachmentDecision.IMPORTED:
        label = "current"
        class_name = "is-neutral"
        code = "successful"
        note = ""
    elif decision == models.AttachmentDecision.DUPLICATE:
        label = "current"
        class_name = "is-neutral"
        code = "no-change"
        note = ""
    elif decision in {
        models.AttachmentDecision.FAILED,
        models.AttachmentDecision.QUARANTINED,
    }:
        label = "failed"
        class_name = "is-warning"
        code = "failed"
        note = f"{filename}: {reason}" if filename else reason
    elif is_benign_attachment_diagnostic(diagnostic):
        label = "ignored"
        class_name = "is-neutral"
        code = "ignored"
        note = (
            f"{filename}: ignored non-price file"
            if filename
            else "Ignored non-price file"
        )
    else:
        label = "no valid file"
        class_name = "is-warning"
        code = "no-valid-file"
        note = f"{filename}: {reason}" if filename else reason
    event_dt = diagnostic_check_datetime(diagnostic)
    check_dt = (
        supplier_email_check_datetime(supplier, event_dt) if supplier else event_dt
    )
    return {
        "label": label,
        "class_name": class_name,
        "code": code,
        "note": note,
        "relative": short_relative_datetime(check_dt) if check_dt else "Checked",
        "full": format_local_datetime(check_dt),
        "progress": None,
        "show_time": bool(check_dt),
    }


def normalize_supplier_check_message(message: str, fallback: str = "") -> str:
    note = (message or "").strip()
    if not note:
        return fallback
    lowered = note.lower()
    if lowered.startswith("no matching email"):
        return fallback or "Manual check found no price email since last success"
    return note


def clarify_latest_check_with_last_success(
    latest_check: dict[str, str | int | None | bool],
    last_import: dict[str, str | int],
) -> dict[str, str | int | None | bool]:
    code = str(latest_check.get("code") or "")
    if str(last_import.get("source_code") or "") == "never":
        return latest_check
    if code not in {"failed", "warning", "no-files", "no-change"}:
        return latest_check
    note = str(latest_check.get("note") or "").strip()
    last_success = str(last_import.get("relative") or "").strip()
    if not last_success:
        return latest_check
    latest_check["note"] = (
        f"{note} - last success {last_success}"
        if note
        else f"Last success {last_success}"
    )
    return latest_check


def is_benign_attachment_diagnostic(diagnostic) -> bool:
    return bool(
        diagnostic
        and diagnostic.decision == models.AttachmentDecision.SKIPPED
        and diagnostic.reason_code
        in {
            models.AttachmentReason.INVOICE_OR_REPORT,
            models.AttachmentReason.FILENAME_BLACKLISTED,
            models.AttachmentReason.UNSUPPORTED_EXTENSION,
            models.AttachmentReason.EMPTY_PAYLOAD,
        }
    )


def summarize_latest_files(supplier, latest_run, latest_diagnostic=None) -> str:
    if latest_run:
        if latest_run.processed_files:
            return "Current"
        if latest_run.errors:
            return "Import issue"
        if latest_run.skipped_duplicates:
            return "Current"
        if latest_run.matched_files:
            return "Current"
        return "Current"
    if latest_diagnostic:
        if latest_diagnostic.decision == models.AttachmentDecision.IMPORTED:
            return "Current"
        if latest_diagnostic.decision == models.AttachmentDecision.DUPLICATE:
            return "Current"
        if latest_diagnostic.decision in {
            models.AttachmentDecision.FAILED,
            models.AttachmentDecision.QUARANTINED,
        }:
            return "Import issue"
        if latest_diagnostic.decision == models.AttachmentDecision.SKIPPED:
            if is_benign_attachment_diagnostic(latest_diagnostic):
                return "Ignored non-price file"
            return "Price file found, not imported"
    if supplier.last_email_check_at:
        if supplier.last_email_processed:
            return "Current"
        if supplier.last_email_matched:
            return "Current"
        return "Current"
    return "No check yet"


def build_problem_note(
    supplier,
    latest_check,
    health,
    latest_failed_file=None,
    latest_diagnostic=None,
) -> str:
    health_code = str(health.get("code") or "")
    check_code = str(latest_check.get("code") or "")
    if latest_diagnostic and latest_diagnostic.decision in {
        models.AttachmentDecision.FAILED,
        models.AttachmentDecision.QUARANTINED,
    }:
        message = (latest_diagnostic.message or "").strip()
        filename = latest_diagnostic.filename or "attachment"
        reason = attachment_reason_label(
            latest_diagnostic.reason_code, latest_diagnostic.decision
        )
        if message:
            return f"{filename}: {reason} - {message[:160]}"
        return f"{filename}: {reason}"
    if (
        latest_diagnostic
        and latest_diagnostic.decision == models.AttachmentDecision.DUPLICATE
        and health_code in {"warning", "stale", "critical"}
    ):
        return "Duplicate found, but last import needs update."
    if latest_failed_file:
        filename = latest_failed_file.filename or "file"
        error = (latest_failed_file.error_message or "").strip()
        if error:
            return f"{filename}: {error[:180]}"
        return f"{filename}: import failed"
    if check_code in {"failed", "warning", "no-files", "no-valid-file", "canceled"}:
        return str(latest_check.get("note") or "")
    if health_code in {"warning", "stale", "critical"}:
        return str(health.get("note") or "")
    return ""


def build_supplier_board_summary(rows: list[dict[str, object]]) -> dict[str, int]:
    summary = {
        "total": len(rows),
        "updating": 0,
        "fresh": 0,
        "warning": 0,
        "stale": 0,
        "critical": 0,
    }
    for row in rows:
        if row["is_running"]:
            summary["updating"] += 1
        code = str(row["health_code"])
        if code in summary:
            summary[code] += 1
    return summary


def serialize_supplier_email_status_row(row: dict[str, object]) -> dict[str, object]:
    return {key: row[key] for key in SUPPLIER_EMAIL_STATUS_ROW_KEYS}


def build_supplier_email_status_rows(
    suppliers,
    *,
    latest_successful_imports,
    latest_failed_import_files,
    latest_attachment_diagnostics,
    latest_runs,
    run_streaks,
) -> dict[str, dict[str, object]]:
    rows = {}
    for supplier in suppliers:
        row = build_supplier_board_row(
            supplier=supplier,
            successful_batch=latest_successful_imports.get(supplier.id),
            latest_run=latest_runs.get(supplier.id),
            streak_count=run_streaks.get(supplier.id, 1),
            latest_failed_file=latest_failed_import_files.get(supplier.id),
            latest_diagnostic=latest_attachment_diagnostics.get(supplier.id),
        )
        rows[str(supplier.id)] = serialize_supplier_email_status_row(row)
    return rows


def build_supplier_email_status_payload(
    suppliers,
    *,
    latest_successful_imports=None,
    latest_failed_import_files=None,
    latest_attachment_diagnostics=None,
    latest_runs=None,
    run_streaks=None,
    scanner_status=None,
    worker_busy=False,
) -> dict[str, object]:
    latest_successful_imports = (
        latest_successful_imports
        if latest_successful_imports is not None
        else collect_latest_successful_imports()
    )
    latest_failed_import_files = (
        latest_failed_import_files
        if latest_failed_import_files is not None
        else collect_latest_failed_import_files()
    )
    latest_attachment_diagnostics = (
        latest_attachment_diagnostics
        if latest_attachment_diagnostics is not None
        else collect_latest_attachment_diagnostics()
    )
    if latest_runs is None or run_streaks is None:
        latest_runs, run_streaks = collect_latest_runs_and_streaks()
    scanner = {
        key: value
        for key, value in (scanner_status or {}).items()
        if key != "cron_status"
    }
    rows = build_supplier_email_status_rows(
        suppliers,
        latest_successful_imports=latest_successful_imports,
        latest_failed_import_files=latest_failed_import_files,
        latest_attachment_diagnostics=latest_attachment_diagnostics,
        latest_runs=latest_runs,
        run_streaks=run_streaks,
    )
    return {
        "rows": rows,
        "summary": build_supplier_board_summary(list(rows.values())),
        "scanner": scanner,
        "worker_busy": worker_busy,
    }


def supplier_email_import_status_all_payload(
    *,
    supplier_manager=None,
    expire_func=None,
    scanner_status_func=None,
    worker_busy_func=None,
    payload_builder=build_supplier_email_status_payload,
) -> dict[str, object]:
    if expire_func is None:
        from prices.services.email_import_runs import expire_stale_email_import_runs

        expire_func = expire_stale_email_import_runs
    if scanner_status_func is None:
        from prices.services.autoimport_status import build_autoimport_scan_status

        scanner_status_func = build_autoimport_scan_status
    if worker_busy_func is None:
        from prices.services.email_import_lock import email_import_worker_is_busy

        worker_busy_func = email_import_worker_is_busy

    expire_func()
    supplier_manager = supplier_manager or models.Supplier.objects
    suppliers = list(supplier_manager.order_by("name"))
    return payload_builder(
        suppliers,
        scanner_status=scanner_status_func(),
        worker_busy=worker_busy_func(),
    )


def board_sort_key(row: dict[str, object]) -> tuple:
    return (
        -int(row["last_import_sort_age_seconds"]),
        int(row["health_severity"]),
        str(row["supplier"].name).lower(),
    )


def build_health_info(
    supplier,
    last_import_info,
    latest_check_info,
    streak_count: int = 1,
) -> dict[str, str | int]:
    expected_hours = expected_import_interval_hours(supplier)
    cadence_label = format_interval_hours(expected_hours)
    code = str(latest_check_info.get("code") or "")
    sort_age_seconds = int(last_import_info.get("sort_age_seconds") or 10**12)
    age_hours = sort_age_seconds / 3600 if sort_age_seconds < 10**11 else None

    if not supplier.from_address_pattern:
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"Email route missing - expected every {cadence_label}",
            "code": "critical",
            "severity": 0,
        }

    if last_import_info["source_code"] == "never":
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"No successful import yet - target {cadence_label}",
            "code": "critical",
            "severity": 0,
        }

    if code == "failed" and streak_count >= 2:
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"{streak_count} failed checks in a row - target {cadence_label}",
            "code": "critical",
            "severity": 0,
        }

    if age_hours is not None and age_hours > expected_hours * 3:
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"Late beyond expected {cadence_label}",
            "code": "critical",
            "severity": 0,
        }

    if code == "failed":
        recent_success = str(last_import_info.get("relative") or "").strip()
        note = (
            latest_check_info["note"] or f"Latest check failed - target {cadence_label}"
        )
        if recent_success and age_hours is not None and age_hours <= expected_hours:
            note = f"Recent success {recent_success} - latest email check failed"
        return {
            "label": "warning",
            "class_name": "is-warning",
            "note": note,
            "code": "warning",
            "severity": 2,
        }

    if (
        code == "no-files"
        and streak_count >= 3
        and age_hours is not None
        and age_hours > expected_hours
    ):
        return {
            "label": "stale",
            "class_name": "is-stale",
            "note": f"{streak_count} no-file checks in a row - expected every {cadence_label}",
            "code": "stale",
            "severity": 1,
        }

    if age_hours is not None and age_hours > expected_hours * 2:
        return {
            "label": "stale",
            "class_name": "is-stale",
            "note": f"Past expected cadence {cadence_label}",
            "code": "stale",
            "severity": 1,
        }

    if age_hours is not None and age_hours > expected_hours:
        return {
            "label": "warning",
            "class_name": "is-warning",
            "note": f"Approaching overdue - expected every {cadence_label}",
            "code": "warning",
            "severity": 2,
        }

    return {
        "label": "fresh",
        "class_name": "is-success",
        "note": f"On cadence - expected every {cadence_label}",
        "code": "fresh",
        "severity": 3,
    }


def build_business_health_info(
    supplier,
    last_import_info,
    latest_check_info,
    streak_count: int = 1,
) -> dict[str, str | int]:
    expected_hours = expected_import_interval_hours(supplier)
    cadence_label = format_expected_cadence(supplier)
    code = str(latest_check_info.get("code") or "")
    last_success_dt = last_import_info.get("datetime")
    expected_deadline = add_business_interval(last_success_dt, expected_hours)
    now = timezone.localtime(timezone.now())
    overdue_seconds = (
        max(int((now - expected_deadline).total_seconds()), 0)
        if expected_deadline
        else None
    )
    expected_label = format_expected_deadline(expected_deadline)
    raw_success_age_seconds = last_import_info.get("sort_age_seconds")
    success_age_seconds = int(
        raw_success_age_seconds if raw_success_age_seconds is not None else 10**12
    )
    warning_after_seconds = 4 * 24 * 60 * 60
    stale_after_seconds = 6 * 24 * 60 * 60
    critical_after_seconds = 10 * 24 * 60 * 60
    success_age_days = max(success_age_seconds // (24 * 60 * 60), 0)
    age_note = (
        f"{success_age_days}d since last successful import"
        if success_age_days
        else "Last successful import today"
    )

    if not supplier.from_address_pattern:
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"Email route missing - expected {cadence_label}",
            "code": "critical",
            "severity": 0,
            "expected_at": expected_label,
        }
    if last_import_info["source_code"] == "never":
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"No successful import yet - expected {cadence_label}",
            "code": "critical",
            "severity": 0,
            "expected_at": expected_label,
        }
    if code == "failed" and streak_count >= 2:
        if success_age_seconds < warning_after_seconds:
            return {
                "label": "warning",
                "class_name": "is-warning",
                "note": f"Recent success - {streak_count} failed checks in a row",
                "code": "warning",
                "severity": 2,
                "expected_at": expected_label,
            }
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"{streak_count} failed checks in a row - expected {cadence_label}",
            "code": "critical",
            "severity": 0,
            "expected_at": expected_label,
        }
    if success_age_seconds >= critical_after_seconds:
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"{age_note} - expected {cadence_label}",
            "code": "critical",
            "severity": 0,
            "expected_at": expected_label,
        }
    if code == "failed":
        recent_success = str(last_import_info.get("relative") or "").strip()
        note = (
            latest_check_info["note"]
            or f"Latest check failed - expected {cadence_label}"
        )
        if recent_success and overdue_seconds is not None and overdue_seconds == 0:
            note = f"Recent success {recent_success} - latest email check failed"
        return {
            "label": "warning",
            "class_name": "is-warning",
            "note": note,
            "code": "warning",
            "severity": 2,
            "expected_at": expected_label,
        }
    if (
        code == "no-files"
        and streak_count >= 3
        and success_age_seconds >= warning_after_seconds
    ):
        return {
            "label": "warning",
            "class_name": "is-warning",
            "note": f"{streak_count} no-file checks in a row - {age_note}",
            "code": "warning",
            "severity": 2,
            "expected_at": expected_label,
        }
    if success_age_seconds >= stale_after_seconds:
        return {
            "label": "stale",
            "class_name": "is-stale",
            "note": f"{age_note} - expected {cadence_label}",
            "code": "stale",
            "severity": 1,
            "expected_at": expected_label,
        }
    if success_age_seconds >= warning_after_seconds:
        return {
            "label": "warning",
            "class_name": "is-warning",
            "note": f"{age_note} - expected {cadence_label}",
            "code": "warning",
            "severity": 2,
            "expected_at": expected_label,
        }
    return {
        "label": "fresh",
        "class_name": "is-success",
        "note": "Fresh - warning after 4d without a successful import",
        "code": "fresh",
        "severity": 3,
        "expected_at": expected_label,
    }


def supplier_log_url(supplier_id: int, run=None, batch=None) -> str:
    base_url = str(reverse_lazy("prices:import_detailed_logs"))
    query = urlencode({"supplier": supplier_id})
    anchor = ""
    batch_dt = batch_activity_datetime(batch)
    run_dt = run_activity_datetime(run)
    if run and (not batch_dt or (run_dt and run_dt >= batch_dt)):
        anchor = f"#run-{run.id}"
    elif batch:
        anchor = f"#batch-{batch.id}"
    return f"{base_url}?{query}{anchor}"


def build_supplier_board_row(
    supplier,
    successful_batch,
    latest_run,
    streak_count: int = 1,
    latest_failed_file=None,
    latest_diagnostic=None,
) -> dict[str, object]:
    last_import = build_last_import_info(successful_batch)
    last_success_dt = last_import.get("datetime")
    if latest_diagnostic and last_success_dt:
        diagnostic_dt = diagnostic_activity_datetime(latest_diagnostic)
        if (
            diagnostic_dt
            and diagnostic_dt <= last_success_dt
            and latest_diagnostic.decision != models.AttachmentDecision.IMPORTED
        ):
            latest_diagnostic = None
    if latest_failed_file and last_success_dt:
        failed_dt = failed_file_activity_datetime(latest_failed_file)
        if failed_dt and failed_dt <= last_success_dt:
            latest_failed_file = None
    latest_check = build_latest_check_info(
        supplier, latest_run, streak_count, latest_diagnostic=latest_diagnostic
    )
    health = build_business_health_info(
        supplier, last_import, latest_check, streak_count
    )
    file_summary = summarize_latest_files(supplier, latest_run, latest_diagnostic)
    problem_note = build_problem_note(
        supplier, latest_check, health, latest_failed_file, latest_diagnostic
    )
    return {
        "supplier": supplier,
        "has_email_route": bool(supplier.from_address_pattern),
        "is_running": bool(
            latest_run and latest_run.status == models.EmailImportStatus.RUNNING
        ),
        "expected_interval_label": format_expected_cadence(supplier),
        "expected_at": str(health.get("expected_at") or ""),
        "last_import_relative": str(last_import["relative"]),
        "last_import_full": str(last_import["full"]),
        "last_import_age_class": str(last_import["class_name"]),
        "last_import_note": str(last_import["note"]),
        "last_import_sort_age_seconds": int(last_import["sort_age_seconds"]),
        "check_label": str(latest_check["label"]),
        "check_class": str(latest_check["class_name"]),
        "check_code": str(latest_check["code"]),
        "check_note": str(latest_check["note"]),
        "check_relative": str(latest_check["relative"]),
        "check_full": str(latest_check["full"]),
        "check_progress": latest_check["progress"],
        "check_has_time": bool(latest_check["show_time"]),
        "check_streak_count": streak_count,
        "health_label": str(health["label"]),
        "health_class": str(health["class_name"]),
        "health_code": str(health["code"]),
        "health_note": str(health["note"]),
        "health_severity": int(health["severity"]),
        "file_summary": file_summary,
        "problem_note": problem_note,
        "latest_log_url": supplier_log_url(
            supplier.id, run=latest_run, batch=successful_batch
        ),
        "latest_reason_code": (
            getattr(latest_diagnostic, "reason_code", "") if latest_diagnostic else ""
        ),
        "source_mailbox_folder": (
            f"{latest_diagnostic.mailbox.name if latest_diagnostic.mailbox else ''}"
            f"/{latest_diagnostic.message_folder or ''}"
            if latest_diagnostic
            else ""
        ).strip("/"),
    }


def build_email_run_status(run) -> dict[str, str | int | None]:
    if not run:
        return {
            "label": "idle",
            "class_name": "is-neutral",
            "note": "",
            "code": "idle",
            "progress": None,
        }
    if run.status == models.EmailImportStatus.RUNNING:
        progress = None
        if run.total_messages:
            progress = int((run.processed_messages / run.total_messages) * 100)
        activity = (run.last_message or "").strip()
        if len(activity) > 140:
            activity = f"{activity[:137]}..."
        if progress is not None and activity:
            note = (
                f"{activity} - {run.processed_messages}/{run.total_messages} messages"
            )
        elif progress is not None:
            note = f"{progress}% complete"
        elif activity:
            note = activity
            progress = 8
        elif run.total_messages:
            note = f"{run.processed_messages}/{run.total_messages} messages"
        else:
            note = "Checking mailbox"
            progress = 8
        return {
            "label": "updating",
            "class_name": "is-running",
            "note": note,
            "code": "running",
            "progress": progress,
        }
    if run.status == models.EmailImportStatus.FINISHED:
        if run.processed_files:
            return {
                "label": "current",
                "class_name": "is-neutral",
                "note": "",
                "code": "successful",
                "progress": None,
            }
        if run.errors:
            return {
                "label": "issues",
                "class_name": "is-warning",
                "note": f"{run.errors} error(s) during import",
                "code": "warning",
                "progress": None,
            }
        if not run.matched_files and not run.processed_files:
            return {
                "label": "current",
                "class_name": "is-neutral",
                "note": "",
                "code": "no-change",
                "progress": None,
            }
        if not run.processed_files and run.skipped_duplicates:
            return {
                "label": "current",
                "class_name": "is-neutral",
                "note": "",
                "code": "no-change",
                "progress": None,
            }
        if not run.processed_files:
            return {
                "label": "current",
                "class_name": "is-neutral",
                "note": "",
                "code": "no-change",
                "progress": None,
            }
        return {
            "label": "current",
            "class_name": "is-neutral",
            "note": "",
            "code": "no-change",
            "progress": None,
        }
    if run.status == models.EmailImportStatus.FAILED:
        return {
            "label": "error",
            "class_name": "is-failed",
            "note": run.last_message or "Email import failed",
            "code": "failed",
            "progress": None,
        }
    if run.status == models.EmailImportStatus.CANCELED:
        return {
            "label": "canceled",
            "class_name": "is-neutral",
            "note": run.last_message or "Canceled by user",
            "code": "canceled",
            "progress": None,
        }
    return {
        "label": "unknown",
        "class_name": "is-neutral",
        "note": run.last_message or "Unknown email status",
        "code": "unknown",
        "progress": None,
    }
