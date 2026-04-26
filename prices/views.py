from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.models import Group
from django.urls import reverse, reverse_lazy
from collections import defaultdict
import hashlib
import os
import shlex
import stat
import subprocess
from pathlib import Path
import logging
from urllib.parse import urlencode

from django.utils import timezone
from django.conf import settings
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    FormView,
    View,
    TemplateView,
    UpdateView,
)
from django.http import JsonResponse
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.core.management import call_command
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from datetime import datetime, time
import sys

from decimal import Decimal, InvalidOperation
import re
import unicodedata

from django.db.models import (
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Max,
    Q,
    Value,
    When,
    Window,
)
from django.db.models.functions import Coalesce, RowNumber, TruncDate

from catalog.models import (
    Brand as CatalogBrand,
    Perfume as CatalogPerfume,
    PerfumeVariant as CatalogPerfumeVariant,
)

from . import forms, models
from django.shortcuts import get_object_or_404, redirect

from .services.importer import (
    delete_import_batch,
    mark_import_batch_products_seen,
    process_import_file,
)
from django.contrib import messages

from .services.importer import preview_mapping_file
from .services.background import run_in_background
from .services.cbr_rates import upsert_cbr_markup_rates, upsert_cbr_markup_rates_range
from .services.email_import_lock import email_import_worker_is_busy
from .services import link_importer
from .services.email_importer import _reason_from_error, _validate_spreadsheet_payload
from .services.product_visibility import (
    apply_hidden_product_keywords,
    normalize_hidden_product_keywords,
    parse_hidden_product_keywords,
)


CRON_MARKER = "PERFUMEX_IMPORT_CRON"
PRODUCT_REMOVED_EVENT_PREFIX = "SYSTEM_DEACTIVATE:"
logger = logging.getLogger(__name__)
FRONT_FILTER_KEYS = ("q", "currency", "supplier", "status", "price_min", "price_max", "exclude", "smart")
EMAIL_IMPORT_BUSY_MESSAGE = "Another email import is already running. Wait for it to finish or cancel it first."


def _short_relative_datetime(value) -> str:
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


def _imported_age_class(value) -> str:
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


def _format_local_datetime(value) -> str:
    if not value:
        return ""
    dt = value
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")


def _batch_activity_datetime(batch):
    if not batch:
        return None
    dt = getattr(batch, "updated_at", None) or batch.created_at or batch.received_at
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _run_activity_datetime(run):
    if not run:
        return None
    dt = run.finished_at or run.started_at
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _expected_import_interval_hours(supplier) -> int:
    value = int(getattr(supplier, "expected_import_interval_hours", 0) or 0)
    return value if value > 0 else 24


def _format_interval_hours(hours: int) -> str:
    if hours % 24 == 0:
        days = hours // 24
        return f"{days}d" if days != 1 else "1d"
    return f"{hours}h"


def _format_expected_cadence(supplier) -> str:
    hours = _expected_import_interval_hours(supplier)
    if hours == 24:
        return "daily, weekdays"
    return f"every {_format_interval_hours(hours)}, weekdays"


def _add_business_interval(start, hours: int):
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


def _format_expected_deadline(value) -> str:
    if not value:
        return ""
    return timezone.localtime(value).strftime("%a %d/%m %H:%M")


def _email_import_timeout_seconds() -> int | None:
    timeout_minutes = int(models.ImportSettings.get_solo().supplier_timeout_minutes or 0)
    return timeout_minutes * 60 if timeout_minutes > 0 else None


def _expire_stale_email_import_runs() -> int:
    timeout_seconds = _email_import_timeout_seconds()
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


def _has_running_email_imports(supplier=None) -> bool:
    _expire_stale_email_import_runs()
    if email_import_worker_is_busy():
        return True
    runs = models.EmailImportRun.objects.filter(status=models.EmailImportStatus.RUNNING)
    if supplier is not None:
        runs = runs.filter(supplier=supplier)
    return runs.exists()


def _spawn_management_command(*args: str) -> subprocess.Popen:
    manage_py = Path(__file__).resolve().parent.parent / "manage.py"
    command = [sys.executable, str(manage_py), *args]
    popen_kwargs = {
        "cwd": str(manage_py.parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": os.environ.copy(),
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(command, **popen_kwargs)


def _build_email_run_status(run) -> dict[str, str | int | None]:
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
            note = f"{activity} - {run.processed_messages}/{run.total_messages} messages"
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
            "code": "successful",
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


def _collect_latest_successful_imports() -> dict[int, models.ImportBatch]:
    batches = (
        models.ImportBatch.objects.select_related("supplier", "mailbox")
        .filter(
            status=models.ImportStatus.PROCESSED,
            importfile__file_kind=models.FileKind.PRICE,
        )
        .exclude(message_id__startswith=PRODUCT_REMOVED_EVENT_PREFIX)
        .annotate(updated_at=Coalesce(Max("importfile__processed_at"), "created_at"))
        .order_by("-updated_at", "-received_at", "-created_at", "-id")
        .distinct()
    )
    latest: dict[int, models.ImportBatch] = {}
    for batch in batches:
        if batch.supplier_id not in latest:
            latest[batch.supplier_id] = batch
    return latest


def _collect_latest_failed_import_files() -> dict[int, models.ImportFile]:
    files = (
        models.ImportFile.objects.select_related("import_batch", "import_batch__supplier")
        .filter(status=models.ImportStatus.FAILED)
        .order_by("-import_batch__created_at", "-id")
    )
    latest: dict[int, models.ImportFile] = {}
    for import_file in files:
        supplier_id = import_file.import_batch.supplier_id
        if supplier_id not in latest:
            latest[supplier_id] = import_file
    return latest


def _collect_latest_attachment_diagnostics() -> dict[int, models.EmailAttachmentDiagnostic]:
    diagnostics = (
        models.EmailAttachmentDiagnostic.objects.select_related("supplier")
        .filter(supplier__isnull=False)
        .order_by("-created_at", "-id")
    )
    latest: dict[int, models.EmailAttachmentDiagnostic] = {}
    for diagnostic in diagnostics:
        supplier_id = diagnostic.supplier_id
        if supplier_id not in latest:
            latest[supplier_id] = diagnostic
    return latest


def _collect_active_price_mappings() -> dict[int, models.SupplierFileMapping]:
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


def _collect_latest_runs_and_streaks() -> tuple[dict[int, models.EmailImportRun], dict[int, int]]:
    runs = (
        models.EmailImportRun.objects.select_related("supplier")
        .order_by("-started_at", "-id")
    )
    latest_runs: dict[int, models.EmailImportRun] = {}
    streaks: dict[int, int] = {}
    target_codes: dict[int, str] = {}
    closed: set[int] = set()
    for run in runs:
        supplier_id = run.supplier_id
        code = str(_build_email_run_status(run).get("code") or "unknown")
        if supplier_id not in latest_runs:
            latest_runs[supplier_id] = run
            streaks[supplier_id] = 1
            target_codes[supplier_id] = code
            continue
        if supplier_id in closed:
            continue
        if target_codes.get(supplier_id) == code:
            streaks[supplier_id] += 1
        else:
            closed.add(supplier_id)
    return latest_runs, streaks


def _build_last_import_info(batch) -> dict[str, str | int]:
    batch_dt = _batch_activity_datetime(batch)
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
        "relative": _short_relative_datetime(batch_dt),
        "full": _format_local_datetime(batch_dt),
        "class_name": _imported_age_class(batch_dt),
        "note": note,
        "source_code": source_code,
        "sort_age_seconds": age_seconds,
        "datetime": batch_dt,
    }


def _diagnostic_activity_datetime(diagnostic):
    if not diagnostic:
        return None
    return diagnostic.message_date or diagnostic.created_at


def _diagnostic_check_datetime(diagnostic):
    if not diagnostic:
        return None
    return diagnostic.created_at


def _latest_active_mailbox_check_datetime():
    return models.Mailbox.objects.filter(is_active=True).aggregate(
        latest=Max("last_checked_at")
    )["latest"]


def _supplier_email_check_datetime(supplier, event_dt=None):
    candidates = [supplier.last_email_check_at, event_dt]
    if supplier.from_address_pattern:
        candidates.append(_latest_active_mailbox_check_datetime())
    return max([dt for dt in candidates if dt], default=None)


def _failed_file_activity_datetime(import_file):
    if not import_file:
        return None
    batch = getattr(import_file, "import_batch", None)
    return (
        import_file.processed_at
        or (getattr(batch, "received_at", None) if batch else None)
        or (getattr(batch, "created_at", None) if batch else None)
    )


def _format_event_filename(filename: str, limit: int = 48) -> str:
    filename = (filename or "").strip()
    if not filename:
        return ""
    if len(filename) <= limit:
        return filename
    return f"{filename[: limit - 3]}..."


def _build_latest_check_info(
    supplier,
    run,
    streak_count: int = 1,
    latest_diagnostic=None,
) -> dict[str, str | int | None | bool]:
    diagnostic_dt = _diagnostic_check_datetime(latest_diagnostic)
    fallback_dt = _supplier_email_check_datetime(supplier, diagnostic_dt)
    if run:
        run_dt = _run_activity_datetime(run)
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
                    return _build_diagnostic_event_check(latest_diagnostic)
                return _build_supplier_email_fallback_check(supplier, fallback_dt)
        check_dt = max([dt for dt in (fallback_dt, run_dt) if dt], default=None)
        run_status = _build_email_run_status(run)
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
            "relative": _short_relative_datetime(check_dt) if check_dt else "Checked",
            "full": _format_local_datetime(check_dt),
            "progress": run_status.get("progress"),
            "show_time": bool(check_dt),
        }

    if diagnostic_dt and (not fallback_dt or diagnostic_dt >= fallback_dt):
        return _build_diagnostic_event_check(latest_diagnostic)

    if fallback_dt:
        return _build_supplier_email_fallback_check(supplier, fallback_dt)

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


def _build_supplier_email_fallback_check(supplier, fallback_dt) -> dict[str, str | int | None | bool]:
    if supplier.last_email_processed:
        label = "current"
        class_name = "is-neutral"
        code = "successful"
        note = ""
    elif supplier.last_email_errors:
        label = "failed"
        class_name = "is-warning"
        code = "failed"
        note = f"{supplier.last_email_errors} import issue(s)"
    elif supplier.last_email_matched:
        label = "current"
        class_name = "is-neutral"
        code = "no-change"
        note = ""
    else:
        label = "current"
        class_name = "is-neutral"
        code = "no-change"
        note = ""
    return {
        "label": label,
        "class_name": class_name,
        "code": code,
        "note": note,
        "relative": _short_relative_datetime(fallback_dt),
        "full": _format_local_datetime(fallback_dt),
        "progress": None,
        "show_time": True,
    }


def _is_benign_attachment_diagnostic(diagnostic) -> bool:
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


def _build_diagnostic_event_check(diagnostic) -> dict[str, str | int | None | bool]:
    supplier = diagnostic.supplier
    decision = diagnostic.decision
    filename = _format_event_filename(diagnostic.filename)
    reason = _attachment_reason_label(diagnostic.reason_code, decision)
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
    elif _is_benign_attachment_diagnostic(diagnostic):
        label = "ignored"
        class_name = "is-neutral"
        code = "ignored"
        note = f"{filename}: ignored non-price file" if filename else "Ignored non-price file"
    else:
        label = "no valid file"
        class_name = "is-warning"
        code = "no-valid-file"
        note = f"{filename}: {reason}" if filename else reason
    event_dt = _diagnostic_check_datetime(diagnostic)
    check_dt = _supplier_email_check_datetime(supplier, event_dt) if supplier else event_dt
    return {
        "label": label,
        "class_name": class_name,
        "code": code,
        "note": note,
        "relative": _short_relative_datetime(check_dt) if check_dt else "Checked",
        "full": _format_local_datetime(check_dt),
        "progress": None,
        "show_time": bool(check_dt),
    }


def _normalize_supplier_check_message(message: str, fallback: str = "") -> str:
    note = (message or "").strip()
    if not note:
        return fallback
    lowered = note.lower()
    if lowered.startswith("no matching email"):
        return fallback or "Manual check found no price email since last success"
    return note


def _clarify_latest_check_with_last_success(
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
    latest_check["note"] = f"{note} · last success {last_success}" if note else f"Last success {last_success}"
    return latest_check


def _parse_backlog_remaining(message: str) -> int:
    match = re.search(r"(\d+)", message or "")
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _build_autoimport_scan_status() -> dict[str, object]:
    settings_obj = models.ImportSettings.get_solo()
    mailboxes = list(models.Mailbox.objects.filter(is_active=True).order_by("priority", "id"))
    cron_status = _get_cron_status()
    since = None
    if settings_obj.last_run_at:
        since = settings_obj.last_run_at - timezone.timedelta(minutes=5)
    backlog_qs = models.EmailAttachmentDiagnostic.objects.filter(
        supplier__isnull=True,
        reason_code=models.AttachmentReason.BACKLOG_REMAINING,
    )
    if since:
        backlog_qs = backlog_qs.filter(created_at__gte=since)
    backlog_items = list(backlog_qs.select_related("mailbox").order_by("-created_at", "-id")[:20])
    remaining = sum(_parse_backlog_remaining(item.message) for item in backlog_items)
    latest_backlog = backlog_items[0] if backlog_items else None
    if remaining:
        mode_label = "Backlog catch-up"
        class_name = "is-warning"
        mode_note = f"Processing oldest messages first; {remaining} message(s) remain after the latest run."
    elif settings_obj.last_run_at:
        mode_label = "Live cursor scan"
        class_name = "is-success"
        mode_note = "Cron should scan only messages newer than each mailbox cursor."
    else:
        mode_label = "Not run yet"
        class_name = "is-neutral"
        mode_note = "No automatic mailbox scan has been recorded."
    mailbox_rows = []
    for mailbox in mailboxes:
        mailbox_rows.append(
            {
                "name": mailbox.name,
                "last_checked": _short_relative_datetime(mailbox.last_checked_at)
                if mailbox.last_checked_at
                else "Not checked",
                "last_checked_full": _format_local_datetime(mailbox.last_checked_at),
                "inbox_uid": mailbox.last_inbox_uid or 0,
                "all_mail_uid": mailbox.last_all_mail_uid or 0,
            }
        )
    return {
        "mode_label": mode_label,
        "class_name": class_name,
        "mode_note": mode_note,
        "last_run": _short_relative_datetime(settings_obj.last_run_at)
        if settings_obj.last_run_at
        else "Never",
        "last_run_full": _format_local_datetime(settings_obj.last_run_at),
        "next_target": _format_local_datetime(cron_status.get("next_run_at")),
        "cron_status": cron_status,
        "remaining_backlog": remaining,
        "latest_backlog_mailbox": latest_backlog.mailbox.name
        if latest_backlog and latest_backlog.mailbox
        else "",
        "mailboxes": mailbox_rows,
        "cursor_note": (
            "Cursor means the saved last processed mailbox UID. Normal cron uses it to read only newer emails; "
            "supplier refresh/backfill uses supplier filters and date windows."
        ),
    }


def _build_health_info(supplier, last_import_info, latest_check_info, streak_count: int = 1) -> dict[str, str | int]:
    expected_hours = _expected_import_interval_hours(supplier)
    cadence_label = _format_interval_hours(expected_hours)
    code = str(latest_check_info.get("code") or "")
    sort_age_seconds = int(last_import_info.get("sort_age_seconds") or 10**12)
    age_hours = sort_age_seconds / 3600 if sort_age_seconds < 10**11 else None

    if not supplier.from_address_pattern:
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"Email route missing · expected every {cadence_label}",
            "code": "critical",
            "severity": 0,
        }

    if last_import_info["source_code"] == "never":
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"No successful import yet · target {cadence_label}",
            "code": "critical",
            "severity": 0,
        }

    if code == "failed" and streak_count >= 2:
        return {
            "label": "critical",
            "class_name": "is-critical",
            "note": f"{streak_count} failed checks in a row · target {cadence_label}",
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
        note = latest_check_info["note"] or f"Latest check failed · target {cadence_label}"
        if recent_success and age_hours is not None and age_hours <= expected_hours:
            note = f"Recent success {recent_success} · latest email check failed"
        return {
            "label": "warning",
            "class_name": "is-warning",
            "note": note,
            "code": "warning",
            "severity": 2,
        }

    if code == "no-files" and streak_count >= 3 and age_hours is not None and age_hours > expected_hours:
        return {
            "label": "stale",
            "class_name": "is-stale",
            "note": f"{streak_count} no-file checks in a row · expected every {cadence_label}",
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
            "note": f"Approaching overdue · expected every {cadence_label}",
            "code": "warning",
            "severity": 2,
        }

    return {
        "label": "fresh",
        "class_name": "is-success",
        "note": f"On cadence · expected every {cadence_label}",
        "code": "fresh",
        "severity": 3,
    }


def _build_business_health_info(supplier, last_import_info, latest_check_info, streak_count: int = 1) -> dict[str, str | int]:
    expected_hours = _expected_import_interval_hours(supplier)
    cadence_label = _format_expected_cadence(supplier)
    code = str(latest_check_info.get("code") or "")
    last_success_dt = last_import_info.get("datetime")
    expected_deadline = _add_business_interval(last_success_dt, expected_hours)
    now = timezone.localtime(timezone.now())
    overdue_seconds = (
        max(int((now - expected_deadline).total_seconds()), 0)
        if expected_deadline
        else None
    )
    expected_label = _format_expected_deadline(expected_deadline)
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
        note = latest_check_info["note"] or f"Latest check failed - expected {cadence_label}"
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
    if code == "no-files" and streak_count >= 3 and success_age_seconds >= warning_after_seconds:
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


def _supplier_log_url(supplier_id: int, run=None, batch=None) -> str:
    base_url = str(reverse_lazy("prices:import_detailed_logs"))
    query = urlencode({"supplier": supplier_id})
    anchor = ""
    batch_dt = _batch_activity_datetime(batch)
    run_dt = _run_activity_datetime(run)
    if run and (not batch_dt or (run_dt and run_dt >= batch_dt)):
        anchor = f"#run-{run.id}"
    elif batch:
        anchor = f"#batch-{batch.id}"
    return f"{base_url}?{query}{anchor}"


def _attachment_reason_label(reason_code: str, decision: str = "") -> str:
    reason_labels = dict(models.AttachmentReason.choices)
    decision_labels = dict(models.AttachmentDecision.choices)
    return reason_labels.get(reason_code) or decision_labels.get(decision) or "Attachment decision"


def _summarize_latest_files(supplier, latest_run, latest_diagnostic=None) -> str:
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
            if _is_benign_attachment_diagnostic(latest_diagnostic):
                return "Ignored non-price file"
            return "Price file found, not imported"
    if supplier.last_email_check_at:
        if supplier.last_email_processed:
            return "Current"
        if supplier.last_email_errors:
            return "Import issue"
        if supplier.last_email_matched:
            return "Current"
        return "Current"
    return "No check yet"


def _build_problem_note(supplier, latest_check, health, latest_failed_file=None, latest_diagnostic=None) -> str:
    health_code = str(health.get("code") or "")
    check_code = str(latest_check.get("code") or "")
    if latest_diagnostic and latest_diagnostic.decision in {
        models.AttachmentDecision.FAILED,
        models.AttachmentDecision.QUARANTINED,
    }:
        message = (latest_diagnostic.message or "").strip()
        filename = latest_diagnostic.filename or "attachment"
        reason = _attachment_reason_label(
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


def _build_supplier_board_row(
    supplier,
    successful_batch,
    latest_run,
    streak_count: int = 1,
    latest_failed_file=None,
    latest_diagnostic=None,
) -> dict[str, object]:
    last_import = _build_last_import_info(successful_batch)
    last_success_dt = last_import.get("datetime")
    if latest_diagnostic and last_success_dt:
        diagnostic_dt = _diagnostic_activity_datetime(latest_diagnostic)
        if (
            diagnostic_dt
            and diagnostic_dt <= last_success_dt
            and latest_diagnostic.decision != models.AttachmentDecision.IMPORTED
        ):
            latest_diagnostic = None
    if latest_failed_file and last_success_dt:
        failed_dt = _failed_file_activity_datetime(latest_failed_file)
        if failed_dt and failed_dt <= last_success_dt:
            latest_failed_file = None
    latest_check = _build_latest_check_info(
        supplier, latest_run, streak_count, latest_diagnostic=latest_diagnostic
    )
    health = _build_business_health_info(supplier, last_import, latest_check, streak_count)
    file_summary = _summarize_latest_files(supplier, latest_run, latest_diagnostic)
    problem_note = _build_problem_note(
        supplier, latest_check, health, latest_failed_file, latest_diagnostic
    )
    return {
        "supplier": supplier,
        "has_email_route": bool(supplier.from_address_pattern),
        "is_running": bool(latest_run and latest_run.status == models.EmailImportStatus.RUNNING),
        "expected_interval_label": _format_expected_cadence(supplier),
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
        "latest_log_url": _supplier_log_url(supplier.id, run=latest_run, batch=successful_batch),
        "latest_reason_code": getattr(latest_diagnostic, "reason_code", "") if latest_diagnostic else "",
        "source_mailbox_folder": (
            f"{latest_diagnostic.mailbox.name if latest_diagnostic.mailbox else ''}"
            f"/{latest_diagnostic.message_folder or ''}"
            if latest_diagnostic
            else ""
        ).strip("/"),
    }


def _board_sort_key(row: dict[str, object]) -> tuple:
    check_priority = {
        "failed": 0,
        "warning": 1,
        "not-configured": 2,
        "no-files": 3,
        "no-change": 4,
        "idle": 5,
        "successful": 6,
        "running": 7,
    }
    return (
        int(row["health_severity"]),
        check_priority.get(str(row["check_code"]), 9),
        -int(row["last_import_sort_age_seconds"]),
        str(row["supplier"].name).lower(),
    )


def _build_supplier_board_summary(rows: list[dict[str, object]]) -> dict[str, int]:
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


def _normalize_exclude_terms(raw: str) -> str:
    return normalize_hidden_product_keywords(raw)


def _parse_exclude_terms(raw: str) -> list[str]:
    return parse_hidden_product_keywords(raw)


def _parse_search_query(raw: str) -> tuple[list[str], list[str]]:
    include_tokens: list[str] = []
    exclude_tokens: list[str] = []
    for token in re.split(r"\s+", (raw or "").strip()):
        cleaned = token.strip()
        if not cleaned:
            continue
        if cleaned.startswith("-") and len(cleaned) > 1:
            exclude_tokens.append(cleaned[1:])
        else:
            include_tokens.append(cleaned)
    return include_tokens, exclude_tokens


def _apply_supplier_product_token_filter(queryset, include_tokens: list[str]):
    tokens = [token.strip() for token in include_tokens if token.strip()][:6]
    if not tokens:
        return queryset

    for token in tokens:
        queryset = queryset.filter(
            Q(name__icontains=token) | Q(supplier__name__icontains=token)
        )
    return queryset


def _smart_search_enabled_from_request(request) -> bool:
    raw = (request.GET.get("smart") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _apply_supplier_product_search_filter(queryset, query: str, include_tokens: list[str], request):
    if query and _smart_search_enabled_from_request(request):
        from assistant_linking.services.smart_search import apply_smart_supplier_search

        return apply_smart_supplier_search(queryset, query)
    return _apply_supplier_product_token_filter(queryset, include_tokens)


def _parse_supplier_filter_ids(raw: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for token in re.split(r"[\s,]+", (raw or "").strip()):
        if not token:
            continue
        try:
            value = int(token)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    return ids


def _supplier_filter_ids_from_request(request) -> list[int]:
    raw_values = request.GET.getlist("supplier")
    merged_raw = ",".join([val for val in raw_values if val])
    return _parse_supplier_filter_ids(merged_raw)


def _serialize_supplier_filter_ids(ids: list[int]) -> str:
    return ",".join(str(x) for x in ids)


def _parse_decimal_query_param(raw: str) -> Decimal | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except Exception:
        return None


def _display_price_expression_for_currency(currency: str):
    output_field = DecimalField(max_digits=14, decimal_places=6)
    display_price_expr = F("current_price")
    if currency not in {models.Currency.USD, models.Currency.RUB}:
        return display_price_expr

    rates = _get_latest_rates()
    usd_rub_rate = rates.get((models.Currency.USD, models.Currency.RUB))
    if not usd_rub_rate or usd_rub_rate <= 0:
        return display_price_expr

    rate_value = Value(usd_rub_rate)
    if currency == models.Currency.USD:
        return Case(
            When(currency=models.Currency.USD, then=F("current_price")),
            When(
                currency=models.Currency.RUB,
                then=ExpressionWrapper(
                    F("current_price") / rate_value,
                    output_field=output_field,
                ),
            ),
            default=F("current_price"),
            output_field=output_field,
        )

    return Case(
        When(currency=models.Currency.RUB, then=F("current_price")),
        When(
            currency=models.Currency.USD,
            then=ExpressionWrapper(
                F("current_price") * rate_value,
                output_field=output_field,
            ),
        ),
        default=F("current_price"),
        output_field=output_field,
    )


def _build_supplier_product_sparklines(products) -> dict[int, list[float]]:
    from datetime import timedelta
    from collections import defaultdict

    product_ids = [product.id for product in products if getattr(product, "id", None)]
    if not product_ids:
        return {}

    six_months_ago = timezone.now() - timedelta(days=180)
    raw_snaps = list(
        models.PriceSnapshot.objects
        .filter(supplier_product_id__in=product_ids, recorded_at__gte=six_months_ago)
        .values("supplier_product_id", "price", "recorded_at")
        .order_by("supplier_product_id", "recorded_at")
    )
    product_day_prices: dict = defaultdict(dict)
    for snap in raw_snaps:
        pid = snap["supplier_product_id"]
        day = snap["recorded_at"].date()
        product_day_prices[pid][day] = float(snap["price"])
    return {
        pid: [value for _, value in sorted(days.items())]
        for pid, days in product_day_prices.items()
    }


def _render_product_sparkline_svg(values, delta_dir: str | None = None) -> str:
    width = 200
    height = 32
    pad = 3
    color = "#c8c8c8"
    if delta_dir == "down":
        color = "#22c55e"
    elif delta_dir == "up":
        color = "#ef4444"
    svg_open = (
        f'<svg class="product-sparkline" width="100%" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="none" fill="none" aria-hidden="true">'
    )
    if not values or len(values) < 2:
        mid = f"{height / 2:.1f}"
        return mark_safe(
            svg_open
            + f'<line x1="0" y1="{mid}" x2="{width}" y2="{mid}" stroke="#e2e2e2" stroke-width="1.5"/></svg>'
        )

    min_value = min(values)
    max_value = max(values)
    value_range = max_value - min_value or 1
    points = []
    for index, value in enumerate(values):
        x = pad + (index / (len(values) - 1)) * (width - pad * 2)
        y = (height - pad) - ((value - min_value) / value_range) * (height - pad * 2)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)
    return mark_safe(
        svg_open
        + f'<polyline points="{polyline}" stroke="{color}" stroke-width="1.5" '
          'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def _apply_supplier_price_filter(queryset, request):
    price_min_raw = request.GET.get("price_min", "")
    price_max_raw = request.GET.get("price_max", "")
    price_min = _parse_decimal_query_param(price_min_raw)
    price_max = _parse_decimal_query_param(price_max_raw)
    if price_min is None and price_max is None:
        return queryset, price_min_raw, price_max_raw

    currency = request.GET.get("currency", "").strip() or models.Currency.USD
    display_price_expr = _display_price_expression_for_currency(currency)
    queryset = queryset.annotate(display_price_filter=display_price_expr)
    if price_min is not None:
        queryset = queryset.filter(display_price_filter__gte=price_min)
    if price_max is not None:
        queryset = queryset.filter(display_price_filter__lte=price_max)
    return queryset, price_min_raw, price_max_raw


def _resolve_supplier_exclude_terms(request) -> str:
    raw_from_query = request.GET.get("exclude")
    if raw_from_query is None:
        if not request.user.is_authenticated:
            return ""
        return models.UserPreference.get_for_user(request.user).supplier_exclude_terms or ""
    normalized = _normalize_exclude_terms(raw_from_query)
    if request.user.is_authenticated:
        prefs = models.UserPreference.get_for_user(request.user)
        if (prefs.supplier_exclude_terms or "") != normalized:
            prefs.supplier_exclude_terms = normalized
            prefs.save(update_fields=["supplier_exclude_terms", "updated_at"])
    return normalized


def _collect_front_filter_values(request) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in FRONT_FILTER_KEYS:
        if key == "supplier":
            supplier_values = [val for val in request.GET.getlist("supplier") if val]
            raw = ",".join(supplier_values)
        else:
            raw = (request.GET.get(key, "") or "").strip()
        values[key] = raw.strip() if isinstance(raw, str) else str(raw or "").strip()
    return values


def _has_front_filter_params(request) -> bool:
    return any(key in request.GET for key in FRONT_FILTER_KEYS)


def _save_front_filters_for_user(request) -> None:
    if not request.user.is_authenticated:
        return
    prefs = models.UserPreference.get_for_user(request.user)
    filters = _collect_front_filter_values(request)
    prefs.supplier_front_filters = filters
    if "exclude" in request.GET:
        prefs.supplier_exclude_terms = _normalize_exclude_terms(filters.get("exclude", ""))
    prefs.save(update_fields=["supplier_front_filters", "supplier_exclude_terms", "updated_at"])


def _runner_script_path() -> Path:
    base_dir = Path(settings.BASE_DIR)
    return base_dir.parent / "run_import_emails.sh"


def _render_runner_script() -> str:
    base_dir = Path(settings.BASE_DIR)
    log_dir = base_dir / "logs"
    log_file = log_dir / "perfumex_email_import.log"
    python_bin = Path(sys.executable)
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            f"mkdir -p {shlex.quote(str(log_dir))}",
            f"exec >>{shlex.quote(str(log_file))} 2>&1",
            'echo "=== START $(date \'+%F %T\') ==="',
            f"cd {shlex.quote(str(base_dir))}",
            "if [ -f .env ]; then",
            "  set -a",
            "  . ./.env",
            "  set +a",
            "fi",
            f"{shlex.quote(str(python_bin))} manage.py import_emails",
            "rc=$?",
            'echo "=== END $(date \'+%F %T\') rc=$rc ==="',
            "exit $rc",
            "",
        ]
    )


def _ensure_runner_script() -> Path:
    script_path = _runner_script_path()
    content = _render_runner_script()
    script_path.write_text(content, encoding="utf-8")
    current_mode = script_path.stat().st_mode
    script_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def _read_crontab_lines() -> list[str]:
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").lower()
        if "no crontab" in stderr:
            return []
        raise RuntimeError(result.stderr.strip() or "Failed to read crontab.")
    text = result.stdout or ""
    lines = [line.rstrip("\n") for line in text.splitlines()]
    return lines


def _write_crontab_lines(lines: list[str]) -> None:
    payload = "\n".join(lines).strip("\n")
    if payload:
        payload = payload + "\n"
    subprocess.run(
        ["crontab", "-"],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )


def _cron_minute_expression(interval_minutes: int) -> str:
    interval = max(int(interval_minutes or 5), 1)
    if interval <= 1:
        return "*"
    if interval <= 59:
        return f"*/{interval}"
    return "0"


def _build_cron_line(script_path: Path, interval_minutes: int | None = None) -> str:
    settings_obj = models.ImportSettings.get_solo()
    interval = int(interval_minutes or settings_obj.interval_minutes or 5)
    timeout_seconds = max(1800, interval * 60)
    return (
        f"{_cron_minute_expression(interval)} * * * * "
        "/usr/bin/flock -n /tmp/perfumex_import.lock "
        f"/usr/bin/timeout {timeout_seconds}s /bin/bash {shlex.quote(str(script_path))} "
        f"# {CRON_MARKER}"
    )


def _get_cron_status() -> dict:
    script_path = _runner_script_path()
    expected_line = _build_cron_line(script_path)
    settings_obj = models.ImportSettings.get_solo()
    now = timezone.now()
    next_run_at = None
    late_by_seconds = 0
    grace_seconds = max(300, int(settings_obj.interval_minutes or 5) * 60 // 2)
    if settings_obj.last_run_at:
        next_run_at = settings_obj.last_run_at + timezone.timedelta(
            minutes=settings_obj.interval_minutes
        )
        late_by_seconds = max(int((now - next_run_at).total_seconds()), 0)
    stale = bool(next_run_at and late_by_seconds > grace_seconds)
    try:
        lines = _read_crontab_lines()
        cron_line = next((line for line in lines if CRON_MARKER in line), "")
        return {
            "supported": True,
            "installed": bool(cron_line),
            "line": cron_line,
            "expected_line": expected_line,
            "needs_reinstall": bool(cron_line and cron_line != expected_line),
            "script_path": str(script_path),
            "script_exists": script_path.exists(),
            "log_path": str(Path(settings.BASE_DIR) / "logs" / "perfumex_email_import.log"),
            "stale": stale,
            "late_by_seconds": late_by_seconds,
            "late_by_minutes": late_by_seconds // 60,
        }
    except Exception as exc:
        return {
            "supported": False,
            "installed": False,
            "line": "",
            "expected_line": expected_line,
            "needs_reinstall": False,
            "script_path": str(script_path),
            "script_exists": script_path.exists(),
            "log_path": str(Path(settings.BASE_DIR) / "logs" / "perfumex_email_import.log"),
            "stale": stale,
            "late_by_seconds": late_by_seconds,
            "late_by_minutes": late_by_seconds // 60,
            "error": str(exc),
        }


def _get_supplier_latest_batch_time(supplier: models.Supplier):
    latest = None
    batches = models.ImportBatch.objects.filter(
        supplier=supplier,
        importfile__status=models.ImportStatus.PROCESSED,
        importfile__file_kind=models.FileKind.PRICE,
    ).values_list("received_at", "created_at")
    for received_at, created_at in batches:
        candidate = received_at or created_at
        if candidate and (latest is None or candidate > latest):
            latest = candidate
    return latest


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "prices/dashboard.html"


class DocumentationView(LoginRequiredMixin, TemplateView):
    template_name = "prices/documentation.html"


class UserProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = get_user_model()
    form_class = forms.UserProfileForm
    template_name = "prices/form.html"

    def get_object(self, queryset=None):
        return self.request.user

    def get_success_url(self):
        next_url = self.request.GET.get("next", "").strip()
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return next_url
        if self.request.user.is_staff:
            return reverse_lazy("prices:dashboard")
        return reverse_lazy("viewer_home")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object_name"] = "Profile"
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(form, "password_changed", False):
            update_session_auth_hash(self.request, self.object)
        messages.success(self.request, "Profile updated.")
        return response


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return bool(self.request.user and self.request.user.is_staff)

    def handle_no_permission(self):
        messages.error(self.request, "You do not have access to user management.")
        return redirect("prices:dashboard")


class MutatingPermissionRequiredMixin(PermissionRequiredMixin):
    raise_exception = True


class ModelDeletePermissionMixin(MutatingPermissionRequiredMixin):
    def get_permission_required(self):
        model = getattr(self, "model", None)
        if not model:
            return super().get_permission_required()
        opts = model._meta
        return (f"{opts.app_label}.delete_{opts.model_name}",)


class BaseListView(LoginRequiredMixin, ListView):
    template_name = "prices/list.html"
    paginate_by = 50
    ordering = ("-id",)
    list_display: tuple[str, ...] = ()
    create_url_name = ""
    update_url_name = ""
    delete_url_name = ""
    detail_url_name = ""
    show_create = True
    show_actions = True
    show_action_menu = True

    def get_ordering(self):
        sort_field = self.request.GET.get("sort")
        sort_dir = self.request.GET.get("dir", "asc")
        if sort_field in self.list_display:
            prefix = "-" if sort_dir == "desc" else ""
            return (f"{prefix}{sort_field}",)
        return super().get_ordering()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list_display"] = self.list_display
        context["list_title"] = getattr(
            self, "list_title", self.model._meta.verbose_name_plural.title()
        )
        context["total_count"] = self.get_queryset().count()
        context["current_sort"] = self.request.GET.get("sort", "")
        context["current_dir"] = self.request.GET.get("dir", "asc")
        context["current_q"] = self.request.GET.get("q", "")
        context["create_url_name"] = self.create_url_name
        context["update_url_name"] = self.update_url_name
        context["delete_url_name"] = self.delete_url_name
        context["detail_url_name"] = self.detail_url_name
        context["show_create"] = self.show_create
        context["show_actions"] = self.show_actions
        context["show_action_menu"] = self.show_action_menu
        context["show_search"] = getattr(self, "show_search", False)
        return context


class BaseCreateView(LoginRequiredMixin, CreateView):
    template_name = "prices/form.html"
    success_url_name = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object_name"] = self.model._meta.verbose_name.title()
        return context

    def get_success_url(self):
        return reverse_lazy(self.success_url_name)


class BaseUpdateView(LoginRequiredMixin, UpdateView):
    template_name = "prices/form.html"
    success_url_name = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object_name"] = self.model._meta.verbose_name.title()
        return context

    def get_success_url(self):
        return reverse_lazy(self.success_url_name)


class BaseDeleteView(ModelDeletePermissionMixin, LoginRequiredMixin, DeleteView):
    template_name = "prices/confirm_delete.html"
    success_url_name = ""

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return next_url
        return reverse_lazy(self.success_url_name)


class SupplierListView(BaseListView):
    model = models.Supplier
    ordering = ("name",)
    list_display = (
        "name",
        "code",
        "default_currency",
        "is_active",
        "created_at",
    )
    detail_url_name = "prices:supplier_detail"
    create_url_name = "prices:supplier_create"
    update_url_name = "prices:supplier_update"
    delete_url_name = "prices:supplier_delete"


class SupplierCreateView(BaseCreateView):
    model = models.Supplier
    form_class = forms.SupplierForm
    success_url_name = "prices:supplier_list"



class SupplierUpdateView(BaseUpdateView):
    model = models.Supplier
    form_class = forms.SupplierForm
    success_url_name = "prices:supplier_list"



class SupplierDeleteView(BaseDeleteView):
    model = models.Supplier
    success_url_name = "prices:supplier_list"


class SupplierDetailView(LoginRequiredMixin, DetailView):
    model = models.Supplier
    template_name = "prices/supplier_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mappings"] = models.SupplierFileMapping.objects.filter(
            supplier=self.object
        ).order_by("-id")
        return context


class SupplierOverviewView(LoginRequiredMixin, TemplateView):
    template_name = "prices/supplier_overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        suppliers = list(models.Supplier.objects.order_by("name"))
        latest_successful_imports = _collect_latest_successful_imports()
        latest_failed_import_files = _collect_latest_failed_import_files()
        latest_attachment_diagnostics = _collect_latest_attachment_diagnostics()
        active_price_mappings = _collect_active_price_mappings()
        _expire_stale_email_import_runs()
        latest_runs, run_streaks = _collect_latest_runs_and_streaks()
        rows = []
        import_batches = models.ImportBatch.objects.select_related(
            "supplier", "mailbox"
        ).prefetch_related("importfile_set").annotate(
            updated_at=Coalesce(Max("importfile__processed_at"), "created_at"),
            date_at=Coalesce("received_at", "created_at"),
        )
        supplier_filter_ids = _supplier_filter_ids_from_request(self.request)
        supplier_filter = _serialize_supplier_filter_ids(supplier_filter_ids)
        status_filter = self.request.GET.get("status", "").strip().lower()
        log_sort = self.request.GET.get("log_sort", "date").strip()
        log_dir = self.request.GET.get("log_dir", "desc").strip().lower()
        if log_dir not in {"asc", "desc"}:
            log_dir = "desc"
        if supplier_filter_ids:
            import_batches = import_batches.filter(supplier_id__in=supplier_filter_ids)
        if status_filter == "successful":
            import_batches = import_batches.filter(status=models.ImportStatus.PROCESSED).exclude(
                message_id__startswith=PRODUCT_REMOVED_EVENT_PREFIX
            )
        elif status_filter == "product_removed":
            import_batches = import_batches.filter(
                message_id__startswith=PRODUCT_REMOVED_EVENT_PREFIX
            )
        elif status_filter == "failed":
            import_batches = import_batches.filter(status=models.ImportStatus.FAILED)
        elif status_filter == "pending":
            import_batches = import_batches.filter(status=models.ImportStatus.PENDING)
        if log_sort == "supplier":
            ordering = "supplier__name"
        elif log_sort == "status":
            ordering = "status"
        elif log_sort == "mailbox":
            ordering = "mailbox__name"
        elif log_sort == "updated":
            ordering = "updated_at"
        else:
            log_sort = "date"
            ordering = "received_at"

        if log_sort == "date":
            if log_dir == "asc":
                import_batches = import_batches.order_by(
                    "date_at", "updated_at", "created_at", "id"
                )
            else:
                import_batches = import_batches.order_by(
                    "-date_at", "-updated_at", "-created_at", "-id"
                )
        elif log_sort == "updated":
            if log_dir == "asc":
                import_batches = import_batches.order_by(
                    "updated_at", "date_at", "created_at", "id"
                )
            else:
                import_batches = import_batches.order_by(
                    "-updated_at", "-date_at", "-created_at", "-id"
                )
        else:
            if log_dir == "asc":
                import_batches = import_batches.order_by(ordering, "-created_at")
            else:
                import_batches = import_batches.order_by(f"-{ordering}", "-created_at")
        log_paginator = Paginator(import_batches, 25)
        log_page_number = self.request.GET.get("log_page", "1")
        log_page = log_paginator.get_page(log_page_number)
        for batch in log_page.object_list:
            is_removed = (batch.message_id or "").startswith(PRODUCT_REMOVED_EVENT_PREFIX)
            batch.is_product_removed_event = is_removed
            if is_removed:
                batch.display_status = "product removed"
                batch.row_class = "import-row-removed"
            elif batch.status == models.ImportStatus.PROCESSED:
                batch.display_status = "successful"
                batch.row_class = "import-row-success"
            elif batch.status == models.ImportStatus.FAILED:
                batch.display_status = "failed"
                batch.row_class = "import-row-failed"
            elif batch.status == models.ImportStatus.PENDING:
                batch.display_status = "pending"
                batch.row_class = "import-row-pending"
            else:
                batch.display_status = batch.status
                batch.row_class = ""
        for supplier in suppliers:
            rows.append(
                _build_supplier_board_row(
                    supplier=supplier,
                    successful_batch=latest_successful_imports.get(supplier.id),
                    latest_run=latest_runs.get(supplier.id),
                    streak_count=run_streaks.get(supplier.id, 1),
                    latest_failed_file=latest_failed_import_files.get(supplier.id),
                    latest_diagnostic=latest_attachment_diagnostics.get(supplier.id),
                )
            )
            rows[-1]["has_quick_upload"] = supplier.id in active_price_mappings
        rows.sort(key=_board_sort_key)
        context["rows"] = rows
        context["supplier_summary"] = _build_supplier_board_summary(rows)
        context["autoimport_scan_status"] = _build_autoimport_scan_status()
        context["import_batches"] = log_page
        context["import_log_page"] = log_page
        context["any_running"] = (
            models.EmailImportRun.objects.filter(
                status=models.EmailImportStatus.RUNNING
            ).exists()
            or email_import_worker_is_busy()
        )
        context["supplier_filter"] = supplier_filter
        context["status_filter"] = status_filter
        context["log_sort"] = log_sort
        context["log_dir"] = log_dir
        context["supplier_options"] = suppliers
        context["status_options"] = [
            ("successful", "Successful"),
            ("product_removed", "Product removed"),
            ("failed", "Failed"),
            ("pending", "Pending"),
        ]
        context["import_section"] = "overview"
        context["detailed_logs_url"] = reverse_lazy("prices:import_detailed_logs")
        context["overview_url"] = reverse_lazy("prices:supplier_overview")
        context["current_query"] = self.request.GET.urlencode()
        return context


class ImportDetailedLogsView(LoginRequiredMixin, TemplateView):
    template_name = "prices/import_detailed_logs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supplier_filter_ids = _supplier_filter_ids_from_request(self.request)
        supplier_filter = _serialize_supplier_filter_ids(supplier_filter_ids)
        status_filter = self.request.GET.get("run_status", "").strip()
        batch_status_filter = self.request.GET.get("batch_status", "").strip()
        decision_filter = self.request.GET.get("decision", "").strip()
        reason_filter = self.request.GET.get("reason", "").strip()
        mailbox_filter = self.request.GET.get("mailbox", "").strip()
        filename_filter = self.request.GET.get("filename", "").strip()
        sender_filter = self.request.GET.get("sender", "").strip()
        date_from_raw = self.request.GET.get("date_from", "").strip()
        date_to_raw = self.request.GET.get("date_to", "").strip()
        runs = models.EmailImportRun.objects.select_related("supplier").order_by("-started_at")
        if supplier_filter_ids:
            runs = runs.filter(supplier_id__in=supplier_filter_ids)
        if status_filter:
            runs = runs.filter(status=status_filter)
        paginator = Paginator(runs, 30)
        page = paginator.get_page(self.request.GET.get("page", "1"))
        run_items = list(page.object_list)

        batches = (
            models.ImportBatch.objects.select_related("supplier", "mailbox")
            .prefetch_related("importfile_set", "importfile_set__mapping")
            .order_by("-created_at")
        )
        if supplier_filter_ids:
            batches = batches.filter(supplier_id__in=supplier_filter_ids)
        if batch_status_filter:
            if batch_status_filter == "processed":
                batches = batches.filter(status=models.ImportStatus.PROCESSED)
            elif batch_status_filter == "failed":
                batches = batches.filter(status=models.ImportStatus.FAILED)
            elif batch_status_filter == "pending":
                batches = batches.filter(status=models.ImportStatus.PENDING)
        batch_paginator = Paginator(batches, 20)
        batch_page = batch_paginator.get_page(self.request.GET.get("bpage", "1"))
        batch_items = list(batch_page.object_list)

        diagnostics = models.EmailAttachmentDiagnostic.objects.select_related(
            "supplier", "mailbox", "import_batch", "import_file"
        ).order_by("-created_at", "-id")
        if supplier_filter_ids:
            diagnostics = diagnostics.filter(supplier_id__in=supplier_filter_ids)
        if decision_filter:
            diagnostics = diagnostics.filter(decision=decision_filter)
        if reason_filter:
            diagnostics = diagnostics.filter(reason_code=reason_filter)
        if mailbox_filter:
            diagnostics = diagnostics.filter(mailbox_id=mailbox_filter)
        if filename_filter:
            diagnostics = diagnostics.filter(filename__icontains=filename_filter)
        if sender_filter:
            diagnostics = diagnostics.filter(sender__icontains=sender_filter)
        try:
            if date_from_raw:
                date_from = timezone.make_aware(datetime.fromisoformat(date_from_raw))
                diagnostics = diagnostics.filter(created_at__gte=date_from)
            if date_to_raw:
                date_to = timezone.make_aware(datetime.fromisoformat(date_to_raw)) + timezone.timedelta(days=1)
                diagnostics = diagnostics.filter(created_at__lt=date_to)
        except ValueError:
            pass
        diagnostics_page = Paginator(diagnostics, 40).get_page(
            self.request.GET.get("dpage", "1")
        )

        for run in run_items:
            run.console_log = run.detailed_log or ""
            if run.console_log:
                continue
            started = run.started_at
            finished = run.finished_at or timezone.now()
            related_batches = [
                b
                for b in batch_items
                if b.supplier_id == run.supplier_id
                and b.created_at >= started
                and b.created_at <= finished
            ]
            lines = []
            for batch in related_batches:
                stamp_dt = batch.received_at or batch.created_at
                stamp = timezone.localtime(stamp_dt).strftime("%H:%M:%S") if stamp_dt else "--:--:--"
                mailbox_name = batch.mailbox.name if batch.mailbox else "manual/backfill"
                lines.append(
                    f"[{stamp}] BATCH supplier={batch.supplier.name} status={batch.status} mailbox={mailbox_name}"
                )
                for file_obj in batch.importfile_set.all():
                    file_stamp_dt = file_obj.processed_at or batch.created_at
                    file_stamp = (
                        timezone.localtime(file_stamp_dt).strftime("%H:%M:%S")
                        if file_stamp_dt
                        else "--:--:--"
                    )
                    mapping_name = str(file_obj.mapping) if file_obj.mapping else "-"
                    lines.append(
                        f"[{file_stamp}] FILE status={file_obj.status} kind={file_obj.file_kind} "
                        f"mapping={mapping_name} name='{file_obj.filename}'"
                    )
                    if file_obj.error_message:
                        lines.append(f"[{file_stamp}] ERROR {file_obj.error_message}")
            run.console_log = "\n".join(lines)

        for batch in batch_items:
            lines = []
            stamp_dt = batch.received_at or batch.created_at
            stamp = timezone.localtime(stamp_dt).strftime("%H:%M:%S") if stamp_dt else "--:--:--"
            mailbox_name = batch.mailbox.name if batch.mailbox else "manual/backfill"
            lines.append(
                f"[{stamp}] BATCH supplier={batch.supplier.name} status={batch.status} mailbox={mailbox_name} "
                f"message_id={batch.message_id or '-'}"
            )
            for file_obj in batch.importfile_set.all():
                file_stamp_dt = file_obj.processed_at or batch.created_at
                file_stamp = (
                    timezone.localtime(file_stamp_dt).strftime("%H:%M:%S")
                    if file_stamp_dt
                    else "--:--:--"
                )
                mapping_name = str(file_obj.mapping) if file_obj.mapping else "-"
                lines.append(
                    f"[{file_stamp}] FILE status={file_obj.status} kind={file_obj.file_kind} "
                    f"mapping={mapping_name} name='{file_obj.filename}'"
                )
                if file_obj.error_message:
                    lines.append(f"[{file_stamp}] ERROR {file_obj.error_message}")
            if batch.error_message:
                lines.append(f"[{stamp}] BATCH_ERROR {batch.error_message}")
            batch.console_log = "\n".join(lines)

        page.object_list = run_items
        batch_page.object_list = batch_items

        context["runs_page"] = page
        context["batches_page"] = batch_page
        context["diagnostics_page"] = diagnostics_page
        context["supplier_filter"] = supplier_filter
        context["status_filter"] = status_filter
        context["batch_status_filter"] = batch_status_filter
        context["decision_filter"] = decision_filter
        context["reason_filter"] = reason_filter
        context["mailbox_filter"] = mailbox_filter
        context["filename_filter"] = filename_filter
        context["sender_filter"] = sender_filter
        context["date_from"] = date_from_raw
        context["date_to"] = date_to_raw
        context["supplier_options"] = models.Supplier.objects.order_by("name")
        context["mailbox_options"] = models.Mailbox.objects.order_by("name")
        context["decision_options"] = models.AttachmentDecision.choices
        context["reason_options"] = models.AttachmentReason.choices
        context["run_status_options"] = [
            models.EmailImportStatus.RUNNING,
            models.EmailImportStatus.FINISHED,
            models.EmailImportStatus.FAILED,
            models.EmailImportStatus.CANCELED,
        ]
        context["batch_status_options"] = [
            ("processed", "Processed"),
            ("failed", "Failed"),
            ("pending", "Pending"),
        ]
        context["import_section"] = "detailed_logs"
        context["detailed_logs_url"] = reverse_lazy("prices:import_detailed_logs")
        context["overview_url"] = reverse_lazy("prices:supplier_overview")
        return context


class StuckEmailImportRunsView(LoginRequiredMixin, TemplateView):
    template_name = "prices/stuck_email_import_runs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cutoff = timezone.now() - timezone.timedelta(minutes=30)
        stuck_runs = (
            models.EmailImportRun.objects.select_related("supplier")
            .filter(status=models.EmailImportStatus.RUNNING, updated_at__lt=cutoff)
            .order_by("updated_at", "started_at", "id")
        )
        context["stuck_runs"] = stuck_runs
        context["cutoff"] = cutoff
        context["import_section"] = "stuck_runs"
        context["detailed_logs_url"] = reverse_lazy("prices:import_detailed_logs")
        context["overview_url"] = reverse_lazy("prices:supplier_overview")
        return context

    def post(self, request, *args, **kwargs):
        run_id = request.POST.get("run_id", "").strip()
        if not run_id.isdigit():
            messages.error(request, "Select a valid import run.")
            return redirect("prices:stuck_email_import_runs")
        updated = models.EmailImportRun.objects.filter(
            id=int(run_id),
            status=models.EmailImportStatus.RUNNING,
        ).update(
            status=models.EmailImportStatus.FAILED,
            finished_at=timezone.now(),
            errors=F("errors") + 1,
            last_message="Marked failed from stuck-run recovery.",
        )
        if updated:
            messages.success(request, "Import run marked as failed.")
        else:
            messages.info(request, "Import run is no longer running.")
        return redirect("prices:stuck_email_import_runs")


class ImportSettingsView(LoginRequiredMixin, TemplateView):
    template_name = "prices/import_settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        settings_obj = models.ImportSettings.get_solo()
        context["form"] = forms.ImportSettingsForm(instance=settings_obj)
        context["settings_obj"] = settings_obj
        context["mailboxes"] = models.Mailbox.objects.order_by("name")
        if settings_obj.last_run_at:
            context["next_run_at"] = settings_obj.last_run_at + timezone.timedelta(
                minutes=settings_obj.interval_minutes
            )
        else:
            context["next_run_at"] = None
        context["cron_status"] = _get_cron_status()
        context["import_section"] = "settings"
        context["overview_url"] = reverse_lazy("prices:supplier_overview")
        context["detailed_logs_url"] = reverse_lazy("prices:import_detailed_logs")
        context["import_settings_url"] = reverse_lazy("prices:import_settings")
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "install_cron":
            try:
                settings_obj = models.ImportSettings.get_solo()
                script_path = _ensure_runner_script()
                lines = [line for line in _read_crontab_lines() if CRON_MARKER not in line]
                lines.append(_build_cron_line(script_path, settings_obj.interval_minutes))
                _write_crontab_lines(lines)
                messages.success(request, "Scheduler installed (cron + runner script).")
            except Exception as exc:
                messages.error(request, f"Failed to install scheduler: {exc}")
            return redirect("prices:import_settings")

        if action == "remove_cron":
            try:
                lines = [line for line in _read_crontab_lines() if CRON_MARKER not in line]
                _write_crontab_lines(lines)
                messages.success(request, "Scheduler cron entry removed.")
            except Exception as exc:
                messages.error(request, f"Failed to remove scheduler: {exc}")
            return redirect("prices:import_settings")

        if action == "run_now":
            if _has_running_email_imports():
                messages.info(request, EMAIL_IMPORT_BUSY_MESSAGE)
                return redirect("prices:import_settings")
            try:
                _spawn_management_command("import_emails", "--force")
            except Exception as exc:
                messages.error(request, f"Failed to start email import: {exc}")
            else:
                messages.success(request, "Email import started.")
            return redirect("prices:import_settings")

        settings_obj = models.ImportSettings.get_solo()
        form = forms.ImportSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Import settings updated.")
        else:
            messages.error(request, "Please fix the errors and try again.")
        return redirect("prices:import_settings")


class ImportWizardView(LoginRequiredMixin, FormView):
    template_name = "prices/import_wizard.html"
    form_class = forms.ImportWizardForm
    success_url = reverse_lazy("prices:supplier_overview")

    def get_initial(self):
        initial = super().get_initial()
        supplier_id = self.request.GET.get("supplier")
        file_kind = self.request.GET.get("file_kind")
        if supplier_id:
            initial["supplier"] = supplier_id
        if file_kind:
            initial["file_kind"] = file_kind
        return initial

    def form_valid(self, form):
        supplier = form.cleaned_data["supplier"]
        file_kind = form.cleaned_data["file_kind"]
        upload = form.cleaned_data["file"]

        mapping = (
            models.SupplierFileMapping.objects.filter(
                supplier=supplier, file_kind=file_kind, is_active=True
            )
            .order_by("-id")
            .first()
        )

        import_batch = models.ImportBatch.objects.create(
            supplier=supplier,
            status=models.ImportStatus.PENDING,
            received_at=timezone.now(),
        )

        content_hash = ""
        if upload:
            hasher = hashlib.sha256()
            for chunk in upload.chunks():
                hasher.update(chunk)
            content_hash = hasher.hexdigest()

        import_file = models.ImportFile.objects.create(
            import_batch=import_batch,
            mapping=mapping,
            file_kind=file_kind,
            filename=upload.name if upload else "",
            file=upload,
            content_hash=content_hash,
            status=models.ImportStatus.PENDING,
        )
        try:
            process_import_file(import_file)
            import_batch.status = models.ImportStatus.PROCESSED
            import_batch.save(update_fields=["status"])
        except Exception as exc:
            import_file.status = models.ImportStatus.FAILED
            import_file.error_message = str(exc)
            import_file.save(update_fields=["status", "error_message"])
            import_batch.status = models.ImportStatus.FAILED
            import_batch.error_message = str(exc)
            import_batch.save(update_fields=["status", "error_message"])
        return super().form_valid(form)


class ImportDeleteView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_importbatch"

    def post(self, request, pk):
        import_batch = get_object_or_404(models.ImportBatch, pk=pk)
        next_url = request.POST.get("next", "").strip()
        delete_import_batch(import_batch)
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return redirect(next_url)
        return redirect("prices:supplier_overview")


class ImportDeleteBulkView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_importbatch"

    def post(self, request):
        ids = request.POST.getlist("import_ids")
        if ids:
            batches = models.ImportBatch.objects.filter(id__in=ids)
            for batch in batches:
                delete_import_batch(batch)
        return redirect("prices:supplier_overview")


class ImportDetailView(LoginRequiredMixin, DetailView):
    model = models.ImportBatch
    template_name = "prices/import_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        import_files = self.object.importfile_set.all().order_by("id")
        context["import_files"] = import_files
        context["received_at_display"] = self.object.received_at or self.object.created_at
        updated_at = import_files.aggregate(updated_at=Max("processed_at")).get("updated_at")
        context["updated_at_display"] = updated_at or self.object.created_at
        back_url = self.request.GET.get("next", "").strip()
        if back_url and url_has_allowed_host_and_scheme(
            back_url, allowed_hosts={self.request.get_host()}
        ):
            context["back_url"] = back_url
        else:
            context["back_url"] = reverse_lazy("prices:supplier_overview")
        return context


class SupplierProductCleanupView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_supplierproduct"

    def post(self, request):
        models.SupplierProduct.objects.filter(
            created_import_batch__isnull=True, last_import_batch__isnull=True
        ).delete()
        return redirect("prices:product_list")


class SupplierProductInactiveCleanupView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_supplierproduct"

    def post(self, request):
        supplier_ids = _parse_supplier_filter_ids(request.POST.get("supplier", ""))
        queryset = models.SupplierProduct.objects.filter(is_active=False)
        if supplier_ids:
            queryset = queryset.filter(supplier_id__in=supplier_ids)
        queryset.delete()
        return redirect("prices:product_list")


class SupplierProductBulkDeleteView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_supplierproduct"

    def post(self, request):
        ids = request.POST.getlist("product_ids")
        if ids:
            models.SupplierProduct.objects.filter(id__in=ids).delete()
        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return redirect(next_url)
        return redirect("prices:product_list")


class SupplierProductSearchView(LoginRequiredMixin, View):
    def get(self, request):
        page_size = 100
        query = request.GET.get("q", "").strip()
        include_tokens, inline_exclude_tokens = _parse_search_query(query)
        exclude_raw = _resolve_supplier_exclude_terms(request)
        exclude_terms = _parse_exclude_terms(exclude_raw)
        currency = request.GET.get("currency", "").strip() or models.Currency.USD
        supplier_filter_ids = _supplier_filter_ids_from_request(request)
        status_filter = request.GET.get("status", "").strip().lower() or "all"
        if status_filter not in {"active", "inactive", "all"}:
            status_filter = "all"
        sort_field = request.GET.get("sort") or "current_price"
        sort_dir = request.GET.get("dir", "asc")
        allowed_sorts = {"supplier", "supplier_sku", "name", "current_price", "last_imported_at"}
        if sort_field not in allowed_sorts:
            sort_field = "current_price"
        prefix = "-" if sort_dir == "desc" else ""
        sort_db_field = {
            "supplier": "supplier__name",
            "supplier_sku": "supplier_sku",
            "name": "name",
            "current_price": "current_price",
            "last_imported_at": "last_imported_at",
        }.get(sort_field, "current_price")
        if sort_field == "current_price":
            queryset_currency = currency if currency in {models.Currency.USD, models.Currency.RUB} else ""
        else:
            queryset_currency = ""
        ordering = f"{prefix}{sort_db_field}"

        queryset = (
            models.SupplierProduct.objects.select_related("supplier").only(
                "id",
                "supplier_id",
                "supplier__name",
                "supplier_sku",
                "name",
                "currency",
                "current_price",
                "last_imported_at",
                "is_active",
            )
        )
        if queryset_currency:
            queryset = queryset.annotate(
                display_price_sort=_display_price_expression_for_currency(queryset_currency)
            )
            ordering = f"{prefix}display_price_sort"
        queryset = _apply_supplier_product_search_filter(queryset, query, include_tokens, request)
        for term in inline_exclude_tokens:
            queryset = queryset.exclude(name__icontains=term)
        if supplier_filter_ids:
            queryset = queryset.filter(supplier_id__in=supplier_filter_ids)
        if status_filter == "active":
            queryset = queryset.filter(is_active=True)
        elif status_filter == "inactive":
            queryset = queryset.filter(is_active=False)
        queryset = apply_hidden_product_keywords(queryset, exclude_terms)
        queryset, _, _ = _apply_supplier_price_filter(queryset, request)
        if status_filter == "all":
            queryset = queryset.order_by("-is_active", ordering, "id")
        else:
            queryset = queryset.order_by(ordering, "id")
        page_raw = request.GET.get("page", "1")
        try:
            page_number = max(int(page_raw), 1)
        except (TypeError, ValueError):
            page_number = 1
        offset = (page_number - 1) * page_size
        rows = list(queryset[offset : offset + page_size + 1])
        has_next = len(rows) > page_size
        visible_products = rows[:page_size]
        has_previous = page_number > 1
        items = []
        rates = _get_latest_rates()
        for product in visible_products:
            display_price = product.current_price
            display_currency = product.currency
            if currency:
                display_currency = currency
                display_price = _convert_price(
                    product.current_price, product.currency, currency, rates
                )
            product.display_price = display_price
            product.display_currency = display_currency
        _attach_previous_price_deltas(visible_products, currency, rates)

        sparklines = _build_supplier_product_sparklines(visible_products)

        for product in visible_products:
            imported_at = _short_relative_datetime(product.last_imported_at)
            imported_at_full = (
                timezone.localtime(product.last_imported_at).strftime("%d.%m.%Y %H:%M")
                if product.last_imported_at
                else ""
            )
            original_price = (
                _format_price(product.current_price, product.currency)
                if product.current_price is not None
                else ""
            )
            items.append(
                {
                    "id": product.id,
                    "supplier": product.supplier.name,
                    "supplier_id": product.supplier_id,
                    "supplier_sku": product.supplier_sku,
                    "name": product.name,
                    "current_price": _format_price(product.display_price, product.display_currency),
                    "original_price": original_price,
                    "last_imported_at": imported_at,
                    "last_imported_at_full": imported_at_full,
                    "last_imported_age_class": _imported_age_class(product.last_imported_at),
                    "is_active": product.is_active,
                    "price_delta_direction": product.price_delta_direction,
                    "price_delta_value": (
                        _format_price(product.price_delta_value, product.display_currency)
                        if product.price_delta_value is not None
                        else ""
                    ),
                    "price_delta_percent": (
                        f"{product.price_delta_percent:.2f}%"
                        if product.price_delta_percent is not None
                        else ""
                    ),
                    "sparkline": sparklines.get(product.id, []),
                }
            )

        return JsonResponse(
            {
                "count": None,
                "count_display": (
                    f"{offset + len(items)}+"
                    if has_next
                    else str(offset + len(items))
                ),
                "shown": len(items),
                "page": page_number,
                "num_pages": None,
                "has_next": has_next,
                "has_previous": has_previous,
                "next_page": page_number + 1 if has_next else None,
                "previous_page": page_number - 1 if has_previous else None,
                "items": items,
            }
        )


class SupplierImportView(LoginRequiredMixin, FormView):
    template_name = "prices/supplier_import.html"
    form_class = forms.SupplierImportForm
    valid_sources = {"email", "link", "file"}

    def get_success_url(self):
        return reverse_lazy("prices:supplier_overview")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supplier = get_object_or_404(models.Supplier, pk=self.kwargs["pk"])
        active_source = self.request.GET.get("source", "email")
        if active_source not in self.valid_sources:
            active_source = "email"
        context["supplier"] = supplier
        context["active_import_source"] = active_source
        context["source_form"] = forms.SupplierPriceSourceForm(
            initial={"source_type": models.PriceSourceType.FIXED_LINK}
        )
        context["price_sources"] = supplier.price_sources.order_by("-is_active", "source_type", "id")
        return context

    def get_initial(self):
        initial = super().get_initial()
        supplier = get_object_or_404(models.Supplier, pk=self.kwargs["pk"])
        mapping = (
            models.SupplierFileMapping.objects.filter(
                supplier=supplier, file_kind=models.FileKind.PRICE, is_active=True
            )
            .order_by("-id")
            .first()
        )
        if mapping:
            name_value = mapping.column_map.get("name")
            name_columns = []
            if isinstance(name_value, list):
                name_columns = [str(value) for value in name_value if value]
            elif name_value:
                name_columns = [str(name_value)]
            sheet_selector_parts = []
            if mapping.sheet_names:
                sheet_selector_parts.extend(
                    [name.strip() for name in mapping.sheet_names.split(",") if name.strip()]
                )
            if mapping.sheet_indexes:
                sheet_selector_parts.extend(
                    [idx.strip() for idx in mapping.sheet_indexes.split(",") if idx.strip()]
                )
            initial.update(
                {
                    "sheet_selector": ", ".join(sheet_selector_parts),
                    "header_row": mapping.header_row,
                    "sku_column": mapping.column_map.get("sku"),
                    "name_columns": ",".join(name_columns),
                    "price_column": mapping.column_map.get("price"),
                    "currency_column": mapping.column_map.get("currency"),
                }
            )
        return initial

    def form_valid(self, form):
        supplier = get_object_or_404(models.Supplier, pk=self.kwargs["pk"])
        mapping = _save_supplier_mapping_from_import_form(form, supplier)
        action = self.request.POST.get("action", "upload_import")
        if action == "save_mapping":
            messages.success(request=self.request, message=f"{supplier.name}: mapping saved.")
            active_source = self.request.GET.get("source", "file")
            if active_source not in self.valid_sources:
                active_source = "file"
            return redirect(f"{reverse('prices:supplier_import', args=[supplier.pk])}?{urlencode({'source': active_source})}#mapping-preview")

        upload = form.cleaned_data.get("file")
        if not upload:
            form.add_error("file", "Choose a spreadsheet to upload and import, or use Save mapping.")
            return self.form_invalid(form)
        try:
            _process_supplier_price_upload(supplier, mapping, upload)
        except Exception as exc:
            messages.error(request=self.request, message=f"{supplier.name}: upload failed. {exc}")
        else:
            messages.success(request=self.request, message=f"{supplier.name}: {upload.name} imported.")
        return super().form_valid(form)


def _supplier_import_tab_url(pk, source):
    return f"{reverse('prices:supplier_import', args=[pk])}?{urlencode({'source': source})}"


class SupplierQuickUploadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        mapping = (
            models.SupplierFileMapping.objects.filter(
                supplier=supplier,
                file_kind=models.FileKind.PRICE,
                is_active=True,
            )
            .order_by("-id")
            .first()
        )
        if not mapping:
            messages.info(
                request,
                "Create or confirm the supplier price mapping first.",
            )
            return redirect(_supplier_import_tab_url(pk, "file"))

        upload = request.FILES.get("file")
        if not upload:
            messages.info(request, "Select a file to upload.")
            return redirect("prices:supplier_overview")

        try:
            _process_supplier_price_upload(supplier, mapping, upload)
        except Exception as exc:
            messages.error(request, f"{supplier.name}: upload failed. {exc}")
        else:
            messages.success(request, f"{supplier.name}: {upload.name} imported.")
        return redirect("prices:supplier_overview")


class SupplierPriceSourceCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        form = forms.SupplierPriceSourceForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Link source was not saved. Check the highlighted fields.")
            return redirect(_supplier_import_tab_url(pk, "link"))
        source = form.save(commit=False)
        source.supplier = supplier
        source.save()
        messages.success(request, "Price link source saved.")
        return redirect(_supplier_import_tab_url(pk, "link"))


class SupplierPriceSourceImportView(LoginRequiredMixin, View):
    def post(self, request, pk, source_pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        source = get_object_or_404(
            models.SupplierPriceSource, pk=source_pk, supplier=supplier
        )
        mapping = (
            models.SupplierFileMapping.objects.filter(
                supplier=supplier,
                file_kind=models.FileKind.PRICE,
                is_active=True,
            )
            .order_by("-id")
            .first()
        )
        if not mapping:
            messages.info(request, "Create or confirm the supplier price mapping first.")
            return redirect(_supplier_import_tab_url(pk, "file"))
        try:
            downloaded = link_importer.download_price_source(source)
            result = _process_supplier_price_payload(
                supplier=supplier,
                mapping=mapping,
                filename=downloaded.filename,
                payload=downloaded.payload,
                content_type=downloaded.content_type,
                source_label=f"{source.get_source_type_display()} / {downloaded.provider}",
                source_url=downloaded.source_url,
            )
        except Exception as exc:
            source.last_checked_at = timezone.now()
            source.last_status = "failed"
            source.last_message = str(exc)
            source.save(update_fields=["last_checked_at", "last_status", "last_message"])
            messages.error(request, f"{supplier.name}: link import failed. {exc}")
            return redirect(_supplier_import_tab_url(pk, "link"))

        source.last_checked_at = timezone.now()
        source.last_status = result["status"]
        source.last_message = result["message"]
        source.last_filename = result["filename"]
        source.save(
            update_fields=[
                "last_checked_at",
                "last_status",
                "last_message",
                "last_filename",
            ]
        )
        if result["status"] == "duplicate":
            messages.info(request, f"{supplier.name}: no change, duplicate file {result['filename']}.")
        else:
            messages.success(request, f"{supplier.name}: imported {result['filename']} from link.")
        return redirect(_supplier_import_tab_url(pk, "link"))


class SupplierPriceSourceDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, source_pk):
        source = get_object_or_404(
            models.SupplierPriceSource, pk=source_pk, supplier_id=pk
        )
        source.delete()
        messages.success(request, "Price link source deleted.")
        return redirect(_supplier_import_tab_url(pk, "link"))


class SupplierEmailImportView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        if not supplier.from_address_pattern:
            messages.info(
                request,
                "Supplier has no sender email configured. Set From address pattern first.",
            )
            return redirect("prices:supplier_overview")
        if _has_running_email_imports():
            messages.info(request, EMAIL_IMPORT_BUSY_MESSAGE)
            return redirect("prices:supplier_overview")
        run = models.EmailImportRun.objects.create(
            supplier=supplier, status=models.EmailImportStatus.RUNNING
        )
        try:
            _spawn_management_command("process_email_runs", "--run-id", str(run.id))
        except Exception as exc:
            models.EmailImportRun.objects.filter(id=run.id).update(
                status=models.EmailImportStatus.FAILED,
                finished_at=timezone.now(),
                errors=1,
                last_message=f"Failed to start background import: {exc}",
            )
            messages.error(request, f"Failed to start email import: {exc}")
        return redirect("prices:supplier_overview")


class SupplierEmailBackfillView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        if not supplier.from_address_pattern:
            messages.info(
                request,
                "Supplier has no sender email configured. Set From address pattern first.",
            )
            return redirect("prices:supplier_import", pk=pk)
        if _has_running_email_imports():
            messages.info(request, EMAIL_IMPORT_BUSY_MESSAGE)
            return redirect("prices:supplier_import", pk=pk)

        start_raw = request.POST.get("start_date", "").strip()
        end_raw = request.POST.get("end_date", "").strip()
        if not start_raw:
            messages.info(request, "Start date is required for backfill.")
            return redirect("prices:supplier_import", pk=pk)

        try:
            start_date = datetime.fromisoformat(start_raw).date()
        except ValueError:
            messages.info(request, "Start date is invalid.")
            return redirect("prices:supplier_import", pk=pk)

        end_date = None
        if end_raw:
            try:
                end_date = datetime.fromisoformat(end_raw).date()
            except ValueError:
                messages.info(request, "End date is invalid.")
                return redirect("prices:supplier_import", pk=pk)

        run = models.EmailImportRun.objects.create(
            supplier=supplier,
            status=models.EmailImportStatus.RUNNING,
            last_message=f"Backfill {start_date.isoformat()} to {end_date or 'today'}",
        )
        command_args = [
            "process_email_runs",
            "--run-id",
            str(run.id),
            "--start-date",
            start_date.isoformat(),
        ]
        if end_date:
            command_args.extend(["--end-date", end_date.isoformat()])
        try:
            _spawn_management_command(*command_args)
        except Exception as exc:
            models.EmailImportRun.objects.filter(id=run.id).update(
                status=models.EmailImportStatus.FAILED,
                finished_at=timezone.now(),
                errors=1,
                last_message=f"Failed to start backfill: {exc}",
            )
            messages.error(request, f"Failed to start backfill: {exc}")
        return redirect("prices:supplier_import", pk=pk)


class SupplierEmailBackfillBulkView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.add_emailimportrun"

    def post(self, request):
        if _has_running_email_imports():
            messages.info(request, EMAIL_IMPORT_BUSY_MESSAGE)
            return redirect("prices:supplier_overview")
        supplier_ids = request.POST.getlist("supplier_ids")
        start_raw = request.POST.get("start_date", "").strip()
        end_raw = request.POST.get("end_date", "").strip()
        if not supplier_ids:
            messages.info(request, "Select at least one supplier for backfill.")
            return redirect("prices:supplier_overview")
        if not start_raw:
            messages.info(request, "Start date is required for bulk backfill.")
            return redirect("prices:supplier_overview")

        try:
            start_date = datetime.fromisoformat(start_raw).date()
        except ValueError:
            messages.info(request, "Start date is invalid.")
            return redirect("prices:supplier_overview")

        end_date = None
        if end_raw:
            try:
                end_date = datetime.fromisoformat(end_raw).date()
            except ValueError:
                messages.info(request, "End date is invalid.")
                return redirect("prices:supplier_overview")

        suppliers = list(
            models.Supplier.objects.filter(id__in=supplier_ids, is_active=True)
        )
        if not suppliers:
            messages.info(request, "No valid suppliers selected.")
            return redirect("prices:supplier_overview")
        run_ids = []
        for supplier in suppliers:
            if not supplier.from_address_pattern:
                continue
            run = models.EmailImportRun.objects.create(
                supplier=supplier,
                status=models.EmailImportStatus.RUNNING,
                last_message=f"Bulk backfill {start_date.isoformat()} to {end_date or 'today'}",
            )
            run_ids.append(run.id)
        if not run_ids:
            messages.info(request, "No selected suppliers have sender email configured.")
            return redirect("prices:supplier_overview")
        command_args = ["process_email_runs"]
        for run_id in run_ids:
            command_args.extend(["--run-id", str(run_id)])
        command_args.extend(["--start-date", start_date.isoformat()])
        if end_date:
            command_args.extend(["--end-date", end_date.isoformat()])
        try:
            _spawn_management_command(*command_args)
        except Exception as exc:
            models.EmailImportRun.objects.filter(id__in=run_ids).update(
                status=models.EmailImportStatus.FAILED,
                finished_at=timezone.now(),
                errors=1,
                last_message=f"Failed to start bulk backfill: {exc}",
            )
            messages.error(request, f"Failed to start bulk backfill: {exc}")
        return redirect("prices:supplier_overview")


class SupplierRatesRecalculateView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.change_exchangerate"

    def post(self, request):
        supplier_ids = request.POST.getlist("supplier_ids")
        start_raw = request.POST.get("start_date", "").strip()
        end_raw = request.POST.get("end_date", "").strip()

        start_date = None
        if start_raw:
            try:
                start_date = datetime.fromisoformat(start_raw).date()
            except ValueError:
                messages.info(request, "Start date is invalid.")
                return redirect("prices:supplier_overview")

        end_date = None
        if end_raw:
            try:
                end_date = datetime.fromisoformat(end_raw).date()
            except ValueError:
                messages.info(request, "End date is invalid.")
                return redirect("prices:supplier_overview")

        if start_date and end_date and end_date < start_date:
            messages.info(request, "End date must be on or after start date.")
            return redirect("prices:supplier_overview")

        batches = models.ImportBatch.objects.filter(
            importfile__file_kind=models.FileKind.PRICE,
            importfile__status=models.ImportStatus.PROCESSED,
        ).distinct()
        if supplier_ids:
            batches = batches.filter(supplier_id__in=supplier_ids)

        import_dates = set()
        for batch in batches.only("received_at", "created_at"):
            dt = batch.received_at or batch.created_at
            if not dt:
                continue
            if timezone.is_aware(dt):
                local_date = timezone.localtime(dt).date()
            else:
                local_date = dt.date()
            if start_date and local_date < start_date:
                continue
            if end_date and local_date > end_date:
                continue
            import_dates.add(local_date)

        if not import_dates:
            messages.info(request, "No import dates found for selected filters.")
            return redirect("prices:supplier_overview")

        settings_obj = models.ImportSettings.get_solo()
        synced = 0
        failed = 0
        for rate_date in sorted(import_dates):
            try:
                upsert_cbr_markup_rates(rate_date, settings_obj.cbr_markup_percent)
                synced += 1
            except Exception:
                failed += 1

        if failed:
            messages.warning(
                request,
                f"Rate recalculation finished: {synced} day(s) synced, {failed} failed.",
            )
        else:
            messages.success(
                request,
                f"Rate recalculation finished: {synced} day(s) synced.",
            )
        return redirect("prices:supplier_overview")


class SupplierEmailImportAllView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.add_emailimportrun"

    def post(self, request):
        if _has_running_email_imports():
            messages.info(request, EMAIL_IMPORT_BUSY_MESSAGE)
            return redirect("prices:supplier_overview")
        try:
            _spawn_management_command("import_emails", "--force")
            messages.info(request, "Mailbox scan started.")
        except Exception as exc:
            messages.error(request, f"Failed to start mailbox scan: {exc}")
        return redirect("prices:supplier_overview")


class SupplierPriceReimportAllView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.change_importbatch"

    def post(self, request):
        def _run_reimport():
            call_command("repair_supplier_price_imports", all_suppliers=True)

        run_in_background(_run_reimport, label="bulk-price-reimport")
        messages.success(
            request,
            "Reimport of all processed price files started in background.",
        )
        return redirect("prices:supplier_overview")


class SupplierEmailImportStatusView(LoginRequiredMixin, View):
    def get(self, request, pk):
        _expire_stale_email_import_runs()
        run = (
            models.EmailImportRun.objects.filter(supplier_id=pk)
            .order_by("-started_at")
            .first()
        )
        if not run:
            return JsonResponse({"status": "idle"})
        progress = None
        if run.total_messages:
            progress = int((run.processed_messages / run.total_messages) * 100)
        detailed_log_tail = (run.detailed_log or "")[-8000:]
        return JsonResponse(
            {
                "status": run.status,
                "progress": progress,
                "processed_files": run.processed_files,
                "errors": run.errors,
                "last_message": run.last_message,
                "detailed_log": detailed_log_tail,
            }
        )


class SupplierEmailImportStatusAllView(LoginRequiredMixin, View):
    def get(self, request):
        _expire_stale_email_import_runs()
        suppliers = list(models.Supplier.objects.order_by("name"))
        latest_successful_imports = _collect_latest_successful_imports()
        latest_failed_import_files = _collect_latest_failed_import_files()
        latest_attachment_diagnostics = _collect_latest_attachment_diagnostics()
        latest_runs, run_streaks = _collect_latest_runs_and_streaks()
        rows = {}
        for supplier in suppliers:
            row = _build_supplier_board_row(
                supplier=supplier,
                successful_batch=latest_successful_imports.get(supplier.id),
                latest_run=latest_runs.get(supplier.id),
                streak_count=run_streaks.get(supplier.id, 1),
                latest_failed_file=latest_failed_import_files.get(supplier.id),
                latest_diagnostic=latest_attachment_diagnostics.get(supplier.id),
            )
            rows[str(supplier.id)] = {
                "is_running": row["is_running"],
                "has_email_route": row["has_email_route"],
                "last_import_relative": row["last_import_relative"],
                "last_import_full": row["last_import_full"],
                "last_import_age_class": row["last_import_age_class"],
                "last_import_note": row["last_import_note"],
                "check_label": row["check_label"],
                "check_class": row["check_class"],
                "check_code": row["check_code"],
                "check_note": row["check_note"],
                "check_relative": row["check_relative"],
                "check_full": row["check_full"],
                "check_progress": row["check_progress"],
                "check_has_time": row["check_has_time"],
                "health_label": row["health_label"],
                "health_class": row["health_class"],
                "health_code": row["health_code"],
                "health_note": row["health_note"],
                "expected_at": row["expected_at"],
                "file_summary": row["file_summary"],
                "problem_note": row["problem_note"],
                "latest_reason_code": row["latest_reason_code"],
                "source_mailbox_folder": row["source_mailbox_folder"],
            }
        return JsonResponse(
            {
                "rows": rows,
                "summary": _build_supplier_board_summary(list(rows.values())),
                "scanner": {
                    key: value
                    for key, value in _build_autoimport_scan_status().items()
                    if key != "cron_status"
                },
                "worker_busy": email_import_worker_is_busy(),
            }
        )


class SupplierEmailImportCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
        _expire_stale_email_import_runs()
        supplier = get_object_or_404(models.Supplier, pk=pk)
        updated = models.EmailImportRun.objects.filter(
            supplier=supplier, status=models.EmailImportStatus.RUNNING
        ).update(
            status=models.EmailImportStatus.CANCELED,
            finished_at=timezone.now(),
            last_message="Canceled by user.",
        )
        if updated:
            messages.info(request, "Email import marked as canceled.")
        else:
            messages.info(request, "No running import to cancel.")
        return redirect("prices:supplier_overview")


@method_decorator(require_POST, name="dispatch")
class SupplierMappingPreviewView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if "file" not in request.FILES:
            return JsonResponse({"error": "No file uploaded."}, status=400)
        upload = request.FILES["file"]
        sheet_index = request.POST.get("sheet_index")
        sheet_index_value = None
        if sheet_index and sheet_index.isdigit():
            sheet_index_value = int(sheet_index)
        preview = preview_mapping_file(upload, sheet_index_value)
        return JsonResponse(preview)


class CurrencyRateView(LoginRequiredMixin, TemplateView):
    template_name = "prices/currencies.html"
    paginate_by = 30

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = forms.ExchangeRateForm()
        rates_qs = models.ExchangeRate.objects.filter(
            from_currency=models.Currency.USD,
            to_currency=models.Currency.RUB,
        ).order_by("-rate_date", "-id")
        paginator = Paginator(rates_qs, self.paginate_by)
        page_number = self.request.GET.get("page", "1")
        rates_page = paginator.get_page(page_number)
        context["rates_page"] = rates_page
        context["rates"] = rates_page.object_list
        settings_obj = models.ImportSettings.get_solo()
        context["cbr_markup_form"] = forms.CBRMarkupForm(
            initial={"cbr_markup_percent": settings_obj.cbr_markup_percent}
        )
        context["cbr_range_form"] = forms.CBRSyncRangeForm(
            initial={
                "start_date": timezone.localdate().strftime("%d/%m/%Y"),
                "end_date": timezone.localdate().strftime("%d/%m/%Y"),
            }
        )
        context["settings_obj"] = settings_obj
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "").strip()
        if action in {"save_cbr_markup", "sync_cbr_today"}:
            settings_obj = models.ImportSettings.get_solo()
            markup_form = forms.CBRMarkupForm(request.POST)
            if not markup_form.is_valid():
                context = self.get_context_data()
                context["cbr_markup_form"] = markup_form
                return self.render_to_response(context)
            settings_obj.cbr_markup_percent = markup_form.cleaned_data["cbr_markup_percent"]
            settings_obj.save(update_fields=["cbr_markup_percent"])
            if action == "sync_cbr_today":
                try:
                    usd_rub = upsert_cbr_markup_rates(
                        timezone.localdate(),
                        settings_obj.cbr_markup_percent,
                    )
                    messages.success(
                        request,
                        f"CBR rate synced for today. USD->RUB: {usd_rub}.",
                    )
                except Exception as exc:
                    messages.error(request, f"Failed to sync CBR rate: {exc}")
            else:
                messages.success(request, "CBR markup saved.")
            return redirect("prices:currency_rates")
        if action == "sync_cbr_range":
            settings_obj = models.ImportSettings.get_solo()
            range_form = forms.CBRSyncRangeForm(request.POST)
            if not range_form.is_valid():
                context = self.get_context_data()
                context["cbr_range_form"] = range_form
                return self.render_to_response(context)
            start_date = range_form.cleaned_data["start_date"]
            end_date = range_form.cleaned_data.get("end_date") or start_date

            try:
                upsert_cbr_markup_rates_range(
                    start_date=start_date,
                    end_date=end_date,
                    markup_percent=settings_obj.cbr_markup_percent,
                )
            except Exception as exc:
                messages.error(request, f"Failed to sync CBR range: {exc}")
            else:
                messages.success(
                    request,
                    f"CBR range synced: {start_date} to {end_date}.",
                )
            return redirect("prices:currency_rates")

        form = forms.ExchangeRateForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("prices:currency_rates")
        context = self.get_context_data()
        context["form"] = form
        return self.render_to_response(context)


class CurrencyRateUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        rate = get_object_or_404(models.ExchangeRate, pk=pk)
        form = forms.ExchangeRateForm(request.POST, instance=rate)
        if form.is_valid():
            form.save()
        page = request.POST.get("page", "").strip()
        if page.isdigit():
            return redirect(f"{reverse_lazy('prices:currency_rates')}?page={page}")
        return redirect("prices:currency_rates")


class CurrencyRateDeleteView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_exchangerate"

    def post(self, request, pk):
        rate = get_object_or_404(models.ExchangeRate, pk=pk)
        rate.delete()
        page = request.POST.get("page", "").strip()
        if page.isdigit():
            return redirect(f"{reverse_lazy('prices:currency_rates')}?page={page}")
        return redirect("prices:currency_rates")


class CurrencyRateBulkDeleteView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_exchangerate"

    def post(self, request):
        ids = request.POST.getlist("rate_ids")
        if ids:
            models.ExchangeRate.objects.filter(id__in=ids).delete()
        page = request.POST.get("page", "").strip()
        if page.isdigit():
            return redirect(f"{reverse_lazy('prices:currency_rates')}?page={page}")
        return redirect("prices:currency_rates")


def _get_latest_rates() -> dict[tuple[str, str], Decimal]:
    today = timezone.localdate()
    rates = {}
    today_rates = models.ExchangeRate.objects.filter(rate_date=today).order_by("-id")
    for rate in today_rates:
        key = (rate.from_currency, rate.to_currency)
        if key not in rates:
            rates[key] = rate.rate
    if rates:
        return rates
    for rate in models.ExchangeRate.objects.order_by("-rate_date", "-id"):
        key = (rate.from_currency, rate.to_currency)
        if key not in rates:
            rates[key] = rate.rate
    return rates


def _get_rates_for_date(
    rate_date,
    cache: dict,
) -> dict[tuple[str, str], Decimal]:
    if not rate_date:
        return {}
    if rate_date in cache:
        return cache[rate_date]
    rates: dict[tuple[str, str], Decimal] = {}
    for rate in models.ExchangeRate.objects.filter(rate_date__lte=rate_date).order_by(
        "-rate_date", "-id"
    ):
        key = (rate.from_currency, rate.to_currency)
        if key not in rates:
            rates[key] = rate.rate
    cache[rate_date] = rates
    return rates


def _prime_rates_cache_for_dates(required_dates, cache: dict) -> None:
    if not required_dates:
        return
    missing_dates = sorted(
        {
            d
            for d in required_dates
            if d and d not in cache
        }
    )
    if not missing_dates:
        return

    max_date = missing_dates[-1]
    rate_rows = (
        models.ExchangeRate.objects.filter(rate_date__lte=max_date)
        .order_by("rate_date", "id")
        .values_list("rate_date", "from_currency", "to_currency", "rate")
    )

    current_rates: dict[tuple[str, str], Decimal] = {}
    idx = 0
    rows_len = 0
    rows = list(rate_rows)
    rows_len = len(rows)

    for target_date in missing_dates:
        while idx < rows_len and rows[idx][0] <= target_date:
            _, from_currency, to_currency, rate = rows[idx]
            current_rates[(from_currency, to_currency)] = rate
            idx += 1
        cache[target_date] = dict(current_rates)


def _convert_price(
    price: Decimal | None,
    from_currency: str,
    to_currency: str,
    rates: dict[tuple[str, str], Decimal],
) -> Decimal | None:
    if price is None or not from_currency or not to_currency:
        return price
    if from_currency == to_currency:
        return price
    direct = rates.get((from_currency, to_currency))
    if direct:
        return price * direct
    inverse = rates.get((to_currency, from_currency))
    if inverse and inverse != 0:
        return price / inverse
    return price


def _format_price(price: Decimal | None, currency: str) -> str:
    if price is None:
        return "-"
    symbol = {"USD": "$", "RUB": "\u20BD"}.get((currency or "").upper(), currency)
    return f"{price:.2f} {symbol}"


def _attach_previous_price_deltas(
    products,
    display_currency: str,
    rates: dict[tuple[str, str], Decimal],
) -> None:
    product_list = list(products)
    if not product_list:
        return
    product_ids = [product.id for product in product_list]
    ranked = (
        models.PriceSnapshot.objects.filter(supplier_product_id__in=product_ids)
        .annotate(
            rn=Window(
                expression=RowNumber(),
                partition_by=[F("supplier_product_id")],
                order_by=[F("recorded_at").desc(), F("id").desc()],
            )
        )
        .filter(rn__lte=2)
        .values(
            "supplier_product_id",
            "rn",
            "price",
            "currency",
        )
    )
    snapshots_by_product: dict[int, dict[int, tuple[Decimal, str]]] = defaultdict(dict)
    for row in ranked:
        snapshots_by_product[row["supplier_product_id"]][row["rn"]] = (
            row["price"],
            row["currency"],
        )

    for product in product_list:
        product.price_delta_direction = ""
        product.price_delta_value = None
        product.price_delta_percent = None

        previous = snapshots_by_product.get(product.id, {}).get(2)
        if not previous:
            continue

        current_display = getattr(product, "display_price", None)
        if current_display is None:
            current_display = _convert_price(
                product.current_price, product.currency, display_currency, rates
            )
        previous_price, previous_currency = previous
        previous_display = _convert_price(
            previous_price, previous_currency, display_currency, rates
        )
        if current_display is None or previous_display is None:
            continue

        delta = current_display - previous_display
        if delta == 0:
            continue

        product.price_delta_direction = "up" if delta > 0 else "down"
        product.price_delta_value = abs(delta)
        if previous_display != 0:
            product.price_delta_percent = (abs(delta) / previous_display) * Decimal("100")


def _save_supplier_mapping_from_import_form(form, supplier):
    sheet_selector = (form.cleaned_data.get("sheet_selector") or "").strip()
    sheet_names = []
    sheet_indexes = []
    if sheet_selector:
        for part in [value.strip() for value in sheet_selector.split(",")]:
            if not part:
                continue
            if part.isdigit():
                sheet_indexes.append(part)
            else:
                sheet_names.append(part)
    name_columns = [
        int(value.strip())
        for value in (form.cleaned_data.get("name_columns") or "").split(",")
        if value.strip().isdigit()
    ]
    sku_column = form.cleaned_data.get("sku_column")
    price_column = form.cleaned_data.get("price_column")
    currency_column = form.cleaned_data.get("currency_column")
    if not name_columns or not price_column:
        raise RuntimeError("Mapping must include name and price columns.")
    mapping, _ = models.SupplierFileMapping.objects.update_or_create(
        supplier=supplier,
        file_kind=models.FileKind.PRICE,
        is_active=True,
        defaults={
            "mapping_mode": models.MappingMode.INDEX,
            "sheet_names": ", ".join(sheet_names),
            "sheet_indexes": ", ".join(sheet_indexes),
            "header_row": form.cleaned_data.get("header_row") or 1,
            "column_map": {
                "sku": sku_column or 0,
                "name": name_columns,
                "price": price_column,
                "currency": currency_column or 0,
            },
        },
    )
    return mapping


def _process_supplier_price_upload(supplier, mapping, upload):
    import_batch = models.ImportBatch.objects.create(
        supplier=supplier,
        status=models.ImportStatus.PENDING,
        received_at=timezone.now(),
    )
    content_hash = ""
    if upload:
        hasher = hashlib.sha256()
        for chunk in upload.chunks():
            hasher.update(chunk)
        content_hash = hasher.hexdigest()
    import_file = models.ImportFile.objects.create(
        import_batch=import_batch,
        mapping=mapping,
        file_kind=models.FileKind.PRICE,
        filename=upload.name if upload else "",
        file=upload,
        content_hash=content_hash,
        status=models.ImportStatus.PENDING,
    )
    try:
        process_import_file(import_file)
        import_batch.status = models.ImportStatus.PROCESSED
        import_batch.save(update_fields=["status"])
    except Exception as exc:
        import_file.status = models.ImportStatus.FAILED
        import_file.error_message = str(exc)
        import_file.save(update_fields=["status", "error_message"])
        import_batch.status = models.ImportStatus.FAILED
        import_batch.error_message = str(exc)
        import_batch.save(update_fields=["status", "error_message"])
        raise
    return import_batch


def _process_supplier_price_payload(
    *,
    supplier,
    mapping,
    filename,
    payload,
    content_type="",
    source_label="link",
    source_url="",
    received_at=None,
):
    filename = filename or "downloaded_price.xlsx"
    imported_at = timezone.now()
    content_hash = hashlib.sha256(payload or b"").hexdigest()
    readable, readable_error = _validate_spreadsheet_payload(filename, payload or b"")
    if not readable:
        raise RuntimeError(f"Spreadsheet could not be opened: {readable_error}")

    existing = models.ImportFile.objects.filter(
        content_hash=content_hash,
        status=models.ImportStatus.PROCESSED,
        import_batch__supplier=supplier,
    ).first()
    if existing:
        seen_count = mark_import_batch_products_seen(existing.import_batch, seen_at=imported_at)
        models.EmailAttachmentDiagnostic.objects.create(
            supplier=supplier,
            import_file=existing,
            import_batch=existing.import_batch,
            message_id=source_url[:255],
            message_date=received_at or timezone.now(),
            sender=source_label[:300],
            subject="Price source link",
            filename=filename,
            content_type=content_type[:200],
            size_bytes=len(payload or b""),
            content_hash=content_hash,
            decision=models.AttachmentDecision.DUPLICATE,
            reason_code=models.AttachmentReason.DUPLICATE_HASH,
            message=f"Duplicate price source link file. Refreshed {seen_count} product(s).",
        )
        return {
            "status": "duplicate",
            "message": "Duplicate price file hash.",
            "filename": filename,
            "batch": existing.import_batch,
        }

    import_batch = models.ImportBatch.objects.create(
        supplier=supplier,
        status=models.ImportStatus.PENDING,
        received_at=imported_at,
        message_id=source_url[:255],
    )
    import_file = models.ImportFile.objects.create(
        import_batch=import_batch,
        mapping=mapping,
        file_kind=models.FileKind.PRICE,
        filename=filename,
        content_hash=content_hash,
        status=models.ImportStatus.PENDING,
    )
    import_file.file.save(filename, ContentFile(payload), save=True)
    try:
        process_import_file(import_file)
        import_file.status = models.ImportStatus.PROCESSED
        import_file.save(update_fields=["status"])
        import_batch.status = models.ImportStatus.PROCESSED
        import_batch.save(update_fields=["status"])
        models.EmailAttachmentDiagnostic.objects.create(
            supplier=supplier,
            import_batch=import_batch,
            import_file=import_file,
            message_id=source_url[:255],
            message_date=received_at or imported_at,
            sender=source_label[:300],
            subject="Price source link",
            filename=filename,
            content_type=content_type[:200],
            size_bytes=len(payload or b""),
            content_hash=content_hash,
            decision=models.AttachmentDecision.IMPORTED,
            message="Price source link imported successfully.",
        )
    except Exception as exc:
        reason_code = _reason_from_error(str(exc))
        try:
            if import_file.file:
                import_file.file.delete(save=False)
        except Exception:
            pass
        settings_obj = models.ImportSettings.get_solo()
        import_file.storage_type = models.ImportFileStorage.QUARANTINE
        import_file.status = models.ImportStatus.FAILED
        import_file.reason_code = reason_code
        import_file.quarantine_until = timezone.now() + timezone.timedelta(
            days=int(settings_obj.quarantine_retention_days or 30)
        )
        import_file.error_message = str(exc)
        import_file.file.save(filename, ContentFile(payload), save=True)
        import_file.save(
            update_fields=[
                "storage_type",
                "status",
                "reason_code",
                "quarantine_until",
                "error_message",
            ]
        )
        import_batch.status = models.ImportStatus.FAILED
        import_batch.error_message = str(exc)
        import_batch.save(update_fields=["status", "error_message"])
        models.EmailAttachmentDiagnostic.objects.create(
            supplier=supplier,
            import_batch=import_batch,
            import_file=import_file,
            message_id=source_url[:255],
            message_date=received_at or imported_at,
            sender=source_label[:300],
            subject="Price source link",
            filename=filename,
            content_type=content_type[:200],
            size_bytes=len(payload or b""),
            content_hash=content_hash,
            decision=models.AttachmentDecision.QUARANTINED,
            reason_code=reason_code,
            message=str(exc),
        )
        raise
    return {
        "status": "imported",
        "message": "Imported successfully.",
        "filename": filename,
        "batch": import_batch,
    }


class MailboxListView(BaseListView):
    model = models.Mailbox
    list_display = ("priority", "name", "protocol", "host", "username", "is_active")
    ordering = ("priority", "id")
    create_url_name = "prices:mailbox_create"
    update_url_name = "prices:mailbox_update"
    delete_url_name = "prices:mailbox_delete"
    show_action_menu = False


class MailboxCreateView(BaseCreateView):
    model = models.Mailbox
    form_class = forms.MailboxForm
    success_url_name = "prices:mailbox_list"


class MailboxUpdateView(BaseUpdateView):
    model = models.Mailbox
    form_class = forms.MailboxForm
    success_url_name = "prices:mailbox_list"


class MailboxDeleteView(BaseDeleteView):
    model = models.Mailbox
    success_url_name = "prices:mailbox_list"


class SupplierMailboxRuleListView(BaseListView):
    model = models.SupplierMailboxRule
    list_display = (
        "supplier",
        "mailbox",
        "from_pattern",
        "subject_pattern",
        "filename_pattern",
        "match_price_files",
        "match_stock_files",
        "is_active",
    )
    create_url_name = "prices:mailbox_rule_create"
    update_url_name = "prices:mailbox_rule_update"
    delete_url_name = "prices:mailbox_rule_delete"


class SupplierMailboxRuleCreateView(BaseCreateView):
    model = models.SupplierMailboxRule
    form_class = forms.SupplierMailboxRuleForm
    success_url_name = "prices:mailbox_rule_list"


class SupplierMailboxRuleUpdateView(BaseUpdateView):
    model = models.SupplierMailboxRule
    form_class = forms.SupplierMailboxRuleForm
    success_url_name = "prices:mailbox_rule_list"


class SupplierMailboxRuleDeleteView(BaseDeleteView):
    model = models.SupplierMailboxRule
    success_url_name = "prices:mailbox_rule_list"


class SupplierFileMappingListView(BaseListView):
    model = models.SupplierFileMapping
    list_display = (
        "supplier",
        "file_kind",
        "mapping_mode",
        "sheet_name",
        "sheet_index",
        "is_active",
    )
    create_url_name = "prices:mapping_create"
    update_url_name = "prices:mapping_update"
    delete_url_name = "prices:mapping_delete"


class SupplierFileMappingCreateView(BaseCreateView):
    model = models.SupplierFileMapping
    form_class = forms.SupplierFileMappingForm
    success_url_name = "prices:mapping_list"

    def get_initial(self):
        initial = super().get_initial()
        supplier_id = self.request.GET.get("supplier")
        if supplier_id:
            initial["supplier"] = supplier_id
        return initial


class SupplierFileMappingUpdateView(BaseUpdateView):
    model = models.SupplierFileMapping
    form_class = forms.SupplierFileMappingForm
    success_url_name = "prices:mapping_list"


class SupplierFileMappingDeleteView(BaseDeleteView):
    model = models.SupplierFileMapping
    success_url_name = "prices:mapping_list"


class SupplierProductListView(BaseListView):
    model = models.SupplierProduct
    paginate_by = 100
    list_display = (
        "supplier_sku",
        "name",
        "current_price",
        "supplier",
        "last_imported_at",
    )
    list_title = "Suppliers Products"
    show_create = False
    show_actions = False
    ordering = ("current_price",)
    show_search = True
    show_currency_filter = True
    show_bulk_delete = True
    link_detail = True
    show_status = True
    detail_url_name = "prices:product_detail"
    create_url_name = "prices:product_create"
    update_url_name = "prices:product_update"
    delete_url_name = "prices:product_delete"

    def get_ordering(self):
        sort_field = self.request.GET.get("sort")
        sort_dir = self.request.GET.get("dir", "asc")
        currency = self.request.GET.get("currency", "").strip() or models.Currency.USD
        status_filter = self.request.GET.get("status", "").strip().lower() or "all"
        if status_filter not in {"active", "inactive", "all"}:
            status_filter = "all"
        sort_map = {
            "supplier": "supplier__name",
            "supplier_sku": "supplier_sku",
            "name": "name",
            "current_price": (
                "display_price_sort"
                if currency in {models.Currency.USD, models.Currency.RUB}
                else "current_price"
            ),
            "last_imported_at": "last_imported_at",
        }
        if sort_field not in self.list_display:
            sort_field = "current_price"
            sort_dir = "asc"
        prefix = "-" if sort_dir == "desc" else ""
        sort_expr = f"{prefix}{sort_map.get(sort_field, 'current_price')}"
        if status_filter == "all":
            return ("-is_active", sort_expr, "id")
        return (sort_expr, "id")

    def get_queryset(self):
        queryset = (
            models.SupplierProduct.objects.all()
            .select_related("supplier")
            .only(
                "id",
                "supplier_id",
                "supplier__name",
                "supplier_sku",
                "name",
                "currency",
                "current_price",
                "last_imported_at",
                "is_active",
            )
        )
        currency = self.request.GET.get("currency", "").strip() or models.Currency.USD
        if currency in {models.Currency.USD, models.Currency.RUB}:
            queryset = queryset.annotate(
                display_price_sort=_display_price_expression_for_currency(currency)
            )
        query = self.request.GET.get("q", "").strip()
        include_tokens, inline_exclude_tokens = _parse_search_query(query)
        exclude_raw = _resolve_supplier_exclude_terms(self.request)
        exclude_terms = _parse_exclude_terms(exclude_raw)
        self._exclude_terms_raw = exclude_raw
        supplier_filter_ids = _supplier_filter_ids_from_request(self.request)
        status_filter = self.request.GET.get("status", "").strip().lower() or "all"
        if status_filter not in {"active", "inactive", "all"}:
            status_filter = "all"
        queryset = _apply_supplier_product_search_filter(queryset, query, include_tokens, self.request)
        for term in inline_exclude_tokens:
            queryset = queryset.exclude(name__icontains=term)
        if supplier_filter_ids:
            queryset = queryset.filter(supplier_id__in=supplier_filter_ids)
        if status_filter == "active":
            queryset = queryset.filter(is_active=True)
        elif status_filter == "inactive":
            queryset = queryset.filter(is_active=False)
        queryset = apply_hidden_product_keywords(queryset, exclude_terms)
        queryset, self._price_min_raw, self._price_max_raw = _apply_supplier_price_filter(
            queryset, self.request
        )
        ordering = self.get_ordering()
        if ordering:
            queryset = queryset.order_by(*ordering)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        currency = self.request.GET.get("currency", "").strip() or models.Currency.USD
        supplier_filter_ids = _supplier_filter_ids_from_request(self.request)
        supplier_filter = _serialize_supplier_filter_ids(supplier_filter_ids)
        status_filter = self.request.GET.get("status", "").strip().lower() or "all"
        if status_filter not in {"active", "inactive", "all"}:
            status_filter = "all"
        currency_options = [choice[0] for choice in models.Currency.choices]
        context["currency_filter"] = currency
        context["currency_options"] = currency_options
        context["supplier_filter"] = supplier_filter
        context["supplier_options"] = models.Supplier.objects.order_by("name")
        context["supplier_filter_names"] = []
        if supplier_filter_ids:
            name_map = {
                supplier.id: supplier.name
                for supplier in models.Supplier.objects.filter(id__in=supplier_filter_ids)
            }
            context["supplier_filter_names"] = [
                {"id": sid, "name": name_map.get(sid, f"Supplier #{sid}")}
                for sid in supplier_filter_ids
            ]
        context["status_filter"] = status_filter
        context["status_options"] = [("all", "All"), ("active", "Active"), ("inactive", "Inactive")]
        context["smart_search_enabled"] = _smart_search_enabled_from_request(self.request)
        context["exclude_terms"] = getattr(self, "_exclude_terms_raw", _resolve_supplier_exclude_terms(self.request))
        context["price_min"] = getattr(self, "_price_min_raw", self.request.GET.get("price_min", ""))
        context["price_max"] = getattr(self, "_price_max_raw", self.request.GET.get("price_max", ""))
        context["show_currency_filter"] = self.show_currency_filter
        context["show_cleanup"] = True
        context["show_search"] = getattr(self, "show_search", False)
        context["link_detail"] = getattr(self, "link_detail", False)
        context["show_status"] = getattr(self, "show_status", False)
        context["show_actions"] = getattr(self, "show_actions", False)
        context["show_bulk_delete"] = getattr(self, "show_bulk_delete", False)
        context["search_url"] = reverse_lazy("prices:product_search")
        context["detail_base_url"] = reverse_lazy("prices:product_list")
        if currency:
            rates = _get_latest_rates()
            for product in context["object_list"]:
                product.display_currency = currency
                product.display_price = _convert_price(
                    product.current_price, product.currency, currency, rates
                )
            _attach_previous_price_deltas(context["object_list"], currency, rates)
        sparklines = _build_supplier_product_sparklines(context["object_list"])
        for product in context["object_list"]:
            product.original_price_display = (
                _format_price(product.current_price, product.currency)
                if product.current_price is not None
                else ""
            )
            product.sparkline_svg = _render_product_sparkline_svg(
                sparklines.get(product.id, []),
                getattr(product, "price_delta_direction", ""),
            )
        return context


class ViewerProductListView(SupplierProductListView):
    show_create = False
    show_actions = False
    show_action_menu = False
    detail_url_name = "viewer_product_detail"
    link_detail = True
    update_url_name = ""
    delete_url_name = ""
    create_url_name = ""
    show_bulk_delete = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            prefs = models.UserPreference.get_for_user(request.user)
            if _has_front_filter_params(request):
                _save_front_filters_for_user(request)
            else:
                saved = prefs.supplier_front_filters or {}
                if isinstance(saved, dict):
                    clean = {
                        key: (saved.get(key, "") or "").strip()
                        for key in FRONT_FILTER_KEYS
                        if isinstance(saved.get(key, ""), str) and (saved.get(key, "") or "").strip()
                    }
                    if clean:
                        return redirect(f"{request.path}?{urlencode(clean)}")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["viewer_mode"] = True
        context["search_url"] = reverse_lazy("viewer_product_search")
        context["show_cleanup"] = False
        context["show_bulk_delete"] = False
        context["link_detail"] = True
        context["detail_base_url"] = "/products/"
        return context


class ViewerProductSearchView(SupplierProductSearchView):
    def get(self, request):
        if request.user.is_authenticated:
            _save_front_filters_for_user(request)
        return super().get(request)


class SupplierProductDetailView(LoginRequiredMixin, DetailView):
    model = models.SupplierProduct
    template_name = "prices/product_detail.html"

    def _parse_datetime(self, value: str):
        if not value:
            return None
        try:
            if len(value) == 10:
                date_value = datetime.fromisoformat(value).date()
                return timezone.make_aware(datetime.combine(date_value, time(0, 0)))
            dt_value = datetime.fromisoformat(value)
            if timezone.is_naive(dt_value):
                return timezone.make_aware(dt_value)
            return dt_value
        except ValueError:
            return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        next_url = self.request.GET.get("next", "").strip()
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            context["back_url"] = next_url
        else:
            context["back_url"] = reverse_lazy("prices:product_list")
        start_value = self.request.GET.get("start", "").strip()
        end_value = self.request.GET.get("end", "").strip()
        chart_currency = self.request.GET.get("chart_currency", "original").strip().lower()
        if chart_currency not in {"original", "usd", "rub"}:
            chart_currency = "original"
        context["link_form"] = forms.SupplierProductLinkForm(instance=self.object)
        context["our_product"] = self.object.our_product
        start_dt = self._parse_datetime(start_value)
        end_dt = self._parse_datetime(end_value)
        snapshots = (
            models.PriceSnapshot.objects.filter(supplier_product=self.object)
            .only(
                "id",
                "recorded_at",
                "price",
                "currency",
                "price_rub",
                "price_usd",
                "import_batch_id",
            )
            .order_by("-recorded_at")
        )
        if start_dt:
            snapshots = snapshots.filter(recorded_at__gte=start_dt)
        if end_dt:
            if len(end_value) == 10:
                end_dt = timezone.make_aware(
                    datetime.combine(end_dt.date(), time(23, 59, 59))
                )
            snapshots = snapshots.filter(recorded_at__lte=end_dt)
        tz = timezone.get_current_timezone()
        latest_by_day_qs = (
            snapshots.annotate(local_day=TruncDate("recorded_at", tzinfo=tz))
            .annotate(
                day_rank=Window(
                    expression=RowNumber(),
                    partition_by=[F("local_day")],
                    order_by=[F("recorded_at").desc(), F("id").desc()],
                )
            )
            .filter(day_rank=1)
            .order_by("-recorded_at")
        )
        latest_by_day = list(latest_by_day_qs)
        history_paginator = Paginator(latest_by_day, 100)
        history_page_obj = history_paginator.get_page(
            self.request.GET.get("history_page")
        )
        context["snapshots"] = history_page_obj.object_list
        context["history_page_obj"] = history_page_obj
        context["history_is_paginated"] = history_page_obj.has_other_pages()
        history_query_params = self.request.GET.copy()
        history_query_params.pop("history_page", None)
        context["history_querystring"] = history_query_params.urlencode()
        chart_labels = []
        chart_values = []
        rates_by_date_cache: dict = {}
        required_dates = {timezone.localtime(s.recorded_at).date() for s in latest_by_day}
        _prime_rates_cache_for_dates(required_dates, rates_by_date_cache)
        for snapshot in reversed(latest_by_day):
            snapshot_date = timezone.localtime(snapshot.recorded_at).date()
            rates_for_snapshot = _get_rates_for_date(
                snapshot_date, rates_by_date_cache
            )
            chart_labels.append(
                timezone.localtime(snapshot.recorded_at).strftime("%d/%m/%Y")
            )
            chart_price = snapshot.price
            if chart_currency == "usd":
                chart_price = snapshot.price_usd
                if chart_price is None:
                    chart_price = _convert_price(
                        snapshot.price,
                        snapshot.currency,
                        models.Currency.USD,
                        rates_for_snapshot,
                    )
            elif chart_currency == "rub":
                chart_price = snapshot.price_rub
                if chart_price is None:
                    chart_price = _convert_price(
                        snapshot.price,
                        snapshot.currency,
                        models.Currency.RUB,
                        rates_for_snapshot,
                    )
            if chart_price is None:
                chart_price = snapshot.price
            chart_values.append(float(chart_price))
        for snapshot in history_page_obj.object_list:
            snapshot_date = timezone.localtime(snapshot.recorded_at).date()
            rates_for_snapshot = _get_rates_for_date(
                snapshot_date, rates_by_date_cache
            )
            usd_rub_rate = rates_for_snapshot.get(
                (models.Currency.USD, models.Currency.RUB)
            )
            display_rub = snapshot.price_rub
            if display_rub is None:
                display_rub = _convert_price(
                    snapshot.price,
                    snapshot.currency,
                    models.Currency.RUB,
                    rates_for_snapshot,
                )
            display_usd = snapshot.price_usd
            if display_usd is None:
                display_usd = _convert_price(
                    snapshot.price,
                    snapshot.currency,
                    models.Currency.USD,
                    rates_for_snapshot,
                )
            snapshot.display_price_rub = display_rub
            snapshot.display_price_usd = display_usd
            snapshot.display_exchange_rate = usd_rub_rate
        context["chart_labels"] = chart_labels
        context["chart_values"] = chart_values
        context["chart_currency"] = chart_currency
        context["chart_currency_symbol"] = {
            "original": "",
            "usd": "$",
            "rub": "\u20BD",
        }.get(chart_currency, "")
        context["start_value"] = start_value
        context["end_value"] = end_value
        return context


class ViewerProductDetailView(SupplierProductDetailView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        next_url = self.request.GET.get("next", "").strip()
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            context["back_url"] = next_url
        else:
            context["back_url"] = reverse_lazy("viewer_home")
        context["viewer_mode"] = True
        return context


class SupplierProductLinkView(LoginRequiredMixin, View):
    def post(self, request, pk):
        product = get_object_or_404(models.SupplierProduct, pk=pk)
        form = forms.SupplierProductLinkForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
        return redirect("prices:product_detail", pk=pk)


class SupplierProductCreateView(BaseCreateView):
    model = models.SupplierProduct
    form_class = forms.SupplierProductForm
    success_url_name = "prices:product_list"


class SupplierProductUpdateView(BaseUpdateView):
    model = models.SupplierProduct
    form_class = forms.SupplierProductForm
    success_url_name = "prices:product_list"


class SupplierProductDeleteView(BaseDeleteView):
    model = models.SupplierProduct
    success_url_name = "prices:product_list"


class OurProductListView(LoginRequiredMixin, ListView):
    model = CatalogPerfumeVariant
    template_name = "prices/our_products_catalog.html"
    context_object_name = "variants"
    paginate_by = 50

    search_fields = (
        "perfume__name",
        "perfume__brand__name",
        "perfume__collection_name",
        "perfume__concentration",
        "size_label",
        "packaging",
        "variant_type",
        "sku",
        "ean",
    )

    def _catalog_search_tokens(self, query: str) -> list[str]:
        tokens = [token for token in re.split(r"\s+", query.strip()) if token]
        return tokens if len(tokens) > 1 else []

    def _token_filter(self, token: str) -> Q:
        token_filter = Q()
        for field in self.search_fields:
            token_filter |= Q(**{f"{field}__icontains": token})
        return token_filter

    def get_queryset(self):
        queryset = CatalogPerfumeVariant.objects.select_related("perfume", "perfume__brand")
        query = self.request.GET.get("q", "").strip()
        if query:
            phrase_filter = self._token_filter(query)
            token_filter = Q()
            for token in self._catalog_search_tokens(query):
                token_filter &= self._token_filter(token)
            queryset = queryset.filter(phrase_filter | token_filter)
        return queryset.order_by("perfume__brand__name", "perfume__name", "perfume__concentration", "size_ml", "packaging")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_tab = self.request.GET.get("tab", "products").strip()
        if active_tab not in {"products", "brands", "collections", "concentrations"}:
            active_tab = "products"
        context["total_count"] = context["paginator"].count if context.get("paginator") else len(context["variants"])
        context["search_query"] = self.request.GET.get("q", "").strip()
        context["active_tab"] = active_tab
        context["brand_rows"] = CatalogBrand.objects.annotate(
            perfume_count=Count("perfumes")
        ).order_by("name")
        context["collection_rows"] = (
            CatalogPerfume.objects.exclude(collection_name="")
            .values("collection_name")
            .annotate(perfume_count=Count("id"))
            .order_by("collection_name")
        )
        context["concentration_rows"] = (
            CatalogPerfume.objects.exclude(concentration="")
            .values("concentration")
            .annotate(perfume_count=Count("id"))
            .order_by("concentration")
        )
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "").strip()
        tab = request.POST.get("tab", "products").strip() or "products"
        redirect_url = f"{reverse('prices:our_product_list')}?{urlencode({'tab': tab})}"

        if action == "add_brand":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Brand name is required.")
            else:
                brand, created = CatalogBrand.objects.get_or_create(name=name)
                messages.success(
                    request,
                    f"Brand {'created' if created else 'already exists'}: {brand.name}.",
                )
            return redirect(redirect_url)

        if action == "delete_brand":
            brand = get_object_or_404(CatalogBrand, pk=request.POST.get("brand_id"))
            if CatalogPerfume.objects.filter(brand=brand).exists():
                messages.error(request, f"{brand.name} has products. Move or delete those products first.")
            else:
                brand_name = brand.name
                brand.delete()
                messages.success(request, f"Brand deleted: {brand_name}.")
            return redirect(redirect_url)

        if action in {"rename_collection", "clear_collection"}:
            old_value = request.POST.get("old_value", "").strip()
            new_value = request.POST.get("new_value", "").strip()
            if not old_value:
                messages.error(request, "Select a collection.")
                return redirect(redirect_url)
            if action == "rename_collection":
                if not new_value:
                    messages.error(request, "New collection name is required.")
                else:
                    updated = CatalogPerfume.objects.filter(collection_name=old_value).update(collection_name=new_value)
                    messages.success(request, f"Collection renamed on {updated} products.")
            else:
                updated = CatalogPerfume.objects.filter(collection_name=old_value).update(collection_name="")
                messages.success(request, f"Collection cleared on {updated} products.")
            return redirect(redirect_url)

        if action in {"rename_concentration", "clear_concentration"}:
            old_value = request.POST.get("old_value", "").strip()
            new_value = request.POST.get("new_value", "").strip()
            if not old_value:
                messages.error(request, "Select a concentration.")
                return redirect(redirect_url)
            if action == "rename_concentration":
                if not new_value:
                    messages.error(request, "New concentration name is required.")
                else:
                    updated = CatalogPerfume.objects.filter(concentration=old_value).update(concentration=new_value)
                    messages.success(request, f"Concentration renamed on {updated} products.")
            else:
                updated = CatalogPerfume.objects.filter(concentration=old_value).update(concentration="")
                messages.success(request, f"Concentration cleared on {updated} products.")
            return redirect(redirect_url)

        messages.error(request, "Unknown catalogue action.")
        return redirect(redirect_url)


class OurProductVariantInlineUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        next_url = request.POST.get("next") or ""
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            next_url = reverse_lazy("prices:our_product_list")
        variant = get_object_or_404(
            CatalogPerfumeVariant.objects.select_related("perfume", "perfume__brand"),
            pk=pk,
        )
        brand_name = request.POST.get("brand_name", "").strip()
        perfume_name = request.POST.get("perfume_name", "").strip()
        concentration = request.POST.get("concentration", "").strip()
        size_text = request.POST.get("size_ml", "").strip().lower().replace("ml", "").replace(",", ".").strip()
        packaging = request.POST.get("packaging", "").strip()

        if not brand_name or not perfume_name:
            messages.error(request, "Brand and scent are required.")
            return redirect(next_url)

        brand = CatalogBrand.objects.filter(name__iexact=brand_name).first()
        if not brand:
            brand = CatalogBrand.objects.create(name=brand_name)
        perfume = variant.perfume
        perfume.brand = brand
        perfume.name = perfume_name
        perfume.concentration = concentration
        perfume.save(update_fields=["brand", "name", "concentration", "updated_at"])

        variant.size_ml = None
        variant.size_label = ""
        if size_text:
            try:
                variant.size_ml = Decimal(size_text)
            except (InvalidOperation, ValueError):
                variant.size_label = request.POST.get("size_ml", "").strip()
        variant.is_tester = request.POST.get("is_tester") == "1"
        variant.packaging = packaging
        variant.save(update_fields=["size_ml", "size_label", "is_tester", "packaging", "updated_at"])
        messages.success(request, "Product row updated.")
        return redirect(next_url)


class OurProductCreateView(BaseCreateView):
    model = models.OurProduct
    form_class = forms.OurProductForm
    success_url_name = "prices:our_product_list"


class OurProductUpdateView(BaseUpdateView):
    model = models.OurProduct
    form_class = forms.OurProductForm
    success_url_name = "prices:our_product_list"


class OurProductDeleteView(BaseDeleteView):
    model = models.OurProduct
    success_url_name = "prices:our_product_list"


class OurProductDetailView(LoginRequiredMixin, DetailView):
    model = models.OurProduct
    template_name = "prices/our_product_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        offers = models.SupplierProduct.objects.select_related("supplier").filter(
            our_product=self.object
        )
        offers = apply_hidden_product_keywords(
            offers,
            _parse_exclude_terms(_resolve_supplier_exclude_terms(self.request)),
        )
        context["offers"] = offers
        return context


class ProductLinkingView(LoginRequiredMixin, TemplateView):
    template_name = "prices/product_linking.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supplier_filter_ids = _supplier_filter_ids_from_request(self.request)
        supplier_filter = _serialize_supplier_filter_ids(supplier_filter_ids)
        search_query = self.request.GET.get("q", "").strip()
        supplier_products = models.SupplierProduct.objects.select_related("supplier")
        supplier_products = apply_hidden_product_keywords(
            supplier_products,
            _parse_exclude_terms(_resolve_supplier_exclude_terms(self.request)),
        )
        if supplier_filter_ids:
            supplier_products = supplier_products.filter(supplier_id__in=supplier_filter_ids)
        if search_query:
            supplier_products = supplier_products.filter(
                Q(name__icontains=search_query)
                | Q(supplier_sku__icontains=search_query)
            )
        supplier_products = supplier_products.order_by("name")
        paginator = Paginator(supplier_products, 50)
        page_number = self.request.GET.get("sp_page", "1")
        page = paginator.get_page(page_number)
        context["supplier_products"] = page
        context["supplier_filter"] = supplier_filter
        context["search_query"] = search_query
        context["supplier_options"] = models.Supplier.objects.order_by("name")
        return context


class ProductLinkingSearchView(LoginRequiredMixin, View):
    @staticmethod
    def _norm_text(value: str) -> str:
        value = unicodedata.normalize("NFKC", (value or "")).lower()
        value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    @classmethod
    def _tokens(cls, value: str) -> list[str]:
        txt = cls._norm_text(value)
        if not txt:
            return []
        tokens = [t for t in txt.split(" ") if t]
        return [t for t in tokens if len(t) >= 2]

    @staticmethod
    def _extract_size(value: str) -> str:
        txt = (value or "").lower()
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|мл)", txt)
        if not match:
            return ""
        num = match.group(1).replace(",", ".")
        unit = match.group(2)
        return f"{num}{unit}"

    @classmethod
    def _score_candidate(
        cls,
        source_name: str,
        source_brand: str,
        source_size: str,
        cand_name: str,
        cand_brand: str,
        cand_size: str,
    ) -> tuple[float, str]:
        src_tokens = set(cls._tokens(source_name))
        cand_tokens = set(cls._tokens(cand_name))
        if not src_tokens or not cand_tokens:
            return 0.0, "no tokens"

        overlap = len(src_tokens.intersection(cand_tokens))
        union = len(src_tokens.union(cand_tokens)) or 1
        token_score = overlap / union

        reasons = []
        score = token_score * 0.72
        reasons.append(f"tokens {overlap}/{union}")

        src_brand_n = cls._norm_text(source_brand)
        cand_brand_n = cls._norm_text(cand_brand)
        if src_brand_n and cand_brand_n:
            if src_brand_n == cand_brand_n:
                score += 0.20
                reasons.append("brand exact")
            elif src_brand_n in cand_brand_n or cand_brand_n in src_brand_n:
                score += 0.10
                reasons.append("brand partial")

        src_size_n = cls._extract_size(source_size) or cls._extract_size(source_name)
        cand_size_n = cls._extract_size(cand_size) or cls._extract_size(cand_name)
        if src_size_n and cand_size_n:
            if src_size_n == cand_size_n:
                score += 0.08
                reasons.append("size exact")
            elif src_size_n.split("ml")[0] == cand_size_n.split("ml")[0]:
                score += 0.04
                reasons.append("size near")

        if score > 1:
            score = 1.0
        return float(score), ", ".join(reasons)

    def get(self, request):
        supplier_product_id = request.GET.get("supplier_product", "").strip()
        terms = (request.GET.get("terms", "") or request.GET.get("q", "")).strip()
        auto = request.GET.get("auto", "").strip() == "1"
        try:
            supplier_product_id = int(supplier_product_id)
        except ValueError:
            return JsonResponse({"error": "Invalid supplier product."}, status=400)
        supplier_product = models.SupplierProduct.objects.select_related("supplier").filter(
            id=supplier_product_id
        ).first()
        if not supplier_product:
            return JsonResponse({"error": "Supplier product not found."}, status=404)
        if auto and not terms:
            terms = supplier_product.name or ""
        tokens = [token for token in re.split(r"[\\s,]+", terms) if token]
        hidden_terms = _parse_exclude_terms(_resolve_supplier_exclude_terms(request))
        our_products = models.OurProduct.objects.all()
        other_supplier_products = models.SupplierProduct.objects.select_related("supplier").exclude(
            supplier_id=supplier_product.supplier_id
        )
        other_supplier_products = apply_hidden_product_keywords(
            other_supplier_products,
            hidden_terms,
        )
        for token in tokens:
            our_products = our_products.filter(
                Q(name__icontains=token)
                | Q(brand__icontains=token)
                | Q(size__icontains=token)
            )
            other_supplier_products = other_supplier_products.filter(
                Q(name__icontains=token) | Q(supplier_sku__icontains=token)
            )
        scored_our = []
        for item in our_products.order_by("name")[:250]:
            score, reason = self._score_candidate(
                supplier_product.name,
                supplier_product.brand,
                supplier_product.size,
                item.name,
                item.brand,
                item.size,
            )
            if score <= 0:
                continue
            scored_our.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "brand": item.brand,
                    "size": item.size,
                    "score": round(score * 100, 1),
                    "reason": reason,
                }
            )
        scored_our.sort(key=lambda x: x["score"], reverse=True)
        our_items = scored_our[:50]

        scored_supplier = []
        for item in other_supplier_products.order_by("name")[:250]:
            score, reason = self._score_candidate(
                supplier_product.name,
                supplier_product.brand,
                supplier_product.size,
                item.name,
                item.brand,
                item.size,
            )
            if score <= 0:
                continue
            scored_supplier.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "supplier": item.supplier.name,
                    "sku": item.supplier_sku,
                    "our_product_id": item.our_product_id,
                    "score": round(score * 100, 1),
                    "reason": reason,
                }
            )
        scored_supplier.sort(key=lambda x: x["score"], reverse=True)
        supplier_items = scored_supplier[:50]
        return JsonResponse(
            {
                "our_products": our_items,
                "supplier_products": supplier_items,
                "source": {
                    "id": supplier_product.id,
                    "name": supplier_product.name,
                    "brand": supplier_product.brand,
                    "size": supplier_product.size,
                },
            }
        )


class ProductLinkingApplyView(LoginRequiredMixin, View):
    def post(self, request):
        source_id = request.POST.get("source_id", "").strip()
        target_our = request.POST.get("target_our", "").strip()
        target_supplier = request.POST.get("target_supplier", "").strip()
        try:
            source_id = int(source_id)
        except ValueError:
            return redirect("prices:product_linking")
        source = get_object_or_404(models.SupplierProduct, id=source_id)
        if target_our:
            try:
                target_our_id = int(target_our)
            except ValueError:
                return redirect("prices:product_linking")
            our_product = get_object_or_404(models.OurProduct, id=target_our_id)
            source.our_product = our_product
            source.save(update_fields=["our_product"])
        elif target_supplier:
            try:
                target_supplier_id = int(target_supplier)
            except ValueError:
                return redirect("prices:product_linking")
            target = get_object_or_404(models.SupplierProduct, id=target_supplier_id)
            if target.our_product:
                source.our_product = target.our_product
                source.save(update_fields=["our_product"])
            else:
                new_our = models.OurProduct.objects.create(
                    name=target.name,
                    brand=target.brand,
                    size=target.size,
                )
                target.our_product = new_our
                source.our_product = new_our
                target.save(update_fields=["our_product"])
                source.save(update_fields=["our_product"])
        return redirect("prices:product_linking")


class ImportBatchListView(BaseListView):
    model = models.ImportBatch
    list_display = (
        "supplier",
        "mailbox",
        "message_id",
        "received_at",
        "status",
        "created_at",
    )
    create_url_name = "prices:import_batch_create"
    update_url_name = "prices:import_batch_update"
    delete_url_name = "prices:import_batch_delete"


class ImportBatchCreateView(BaseCreateView):
    model = models.ImportBatch
    form_class = forms.ImportBatchForm
    success_url_name = "prices:import_batch_list"


class ImportBatchUpdateView(BaseUpdateView):
    model = models.ImportBatch
    form_class = forms.ImportBatchForm
    success_url_name = "prices:import_batch_list"


class ImportBatchDeleteView(BaseDeleteView):
    model = models.ImportBatch
    success_url_name = "prices:import_batch_list"


class ImportFileListView(BaseListView):
    model = models.ImportFile
    list_display = (
        "import_batch",
        "mapping",
        "file_kind",
        "filename",
        "status",
        "processed_at",
    )
    create_url_name = "prices:import_file_create"
    update_url_name = "prices:import_file_update"
    delete_url_name = "prices:import_file_delete"


class ImportFileCreateView(BaseCreateView):
    model = models.ImportFile
    form_class = forms.ImportFileForm
    success_url_name = "prices:import_file_list"


class ImportFileUpdateView(BaseUpdateView):
    model = models.ImportFile
    form_class = forms.ImportFileForm
    success_url_name = "prices:import_file_list"


class ImportFileDeleteView(BaseDeleteView):
    model = models.ImportFile
    success_url_name = "prices:import_file_list"


class PriceSnapshotListView(BaseListView):
    model = models.PriceSnapshot
    list_display = ("supplier_product", "price", "currency", "recorded_at")
    create_url_name = "prices:price_snapshot_create"
    update_url_name = "prices:price_snapshot_update"
    delete_url_name = "prices:price_snapshot_delete"


class PriceSnapshotCreateView(BaseCreateView):
    model = models.PriceSnapshot
    form_class = forms.PriceSnapshotForm
    success_url_name = "prices:price_snapshot_list"


class PriceSnapshotUpdateView(BaseUpdateView):
    model = models.PriceSnapshot
    form_class = forms.PriceSnapshotForm
    success_url_name = "prices:price_snapshot_list"


class PriceSnapshotDeleteView(BaseDeleteView):
    model = models.PriceSnapshot
    success_url_name = "prices:price_snapshot_list"


class StockSnapshotListView(BaseListView):
    model = models.StockSnapshot
    list_display = ("supplier_product", "quantity", "recorded_at")
    create_url_name = "prices:stock_snapshot_create"
    update_url_name = "prices:stock_snapshot_update"
    delete_url_name = "prices:stock_snapshot_delete"


class StockSnapshotCreateView(BaseCreateView):
    model = models.StockSnapshot
    form_class = forms.StockSnapshotForm
    success_url_name = "prices:stock_snapshot_list"


class StockSnapshotUpdateView(BaseUpdateView):
    model = models.StockSnapshot
    form_class = forms.StockSnapshotForm
    success_url_name = "prices:stock_snapshot_list"


class StockSnapshotDeleteView(BaseDeleteView):
    model = models.StockSnapshot
    success_url_name = "prices:stock_snapshot_list"


class ExchangeRateListView(BaseListView):
    model = models.ExchangeRate
    list_display = (
        "rate_date",
        "from_currency",
        "to_currency",
        "rate",
        "source",
    )
    create_url_name = "prices:exchange_rate_create"
    update_url_name = "prices:exchange_rate_update"
    delete_url_name = "prices:exchange_rate_delete"


class ExchangeRateCreateView(BaseCreateView):
    model = models.ExchangeRate
    form_class = forms.ExchangeRateForm
    success_url_name = "prices:exchange_rate_list"


class ExchangeRateUpdateView(BaseUpdateView):
    model = models.ExchangeRate
    form_class = forms.ExchangeRateForm
    success_url_name = "prices:exchange_rate_list"


class ExchangeRateDeleteView(BaseDeleteView):
    model = models.ExchangeRate
    success_url_name = "prices:exchange_rate_list"


class UserListView(StaffRequiredMixin, BaseListView):
    model = get_user_model()
    list_display = ("username", "email", "is_staff", "is_active", "date_joined")
    list_title = "Users"
    create_url_name = "prices:user_create"
    update_url_name = "prices:user_update"
    delete_url_name = "prices:user_delete"
    detail_url_name = ""
    show_search = True
    ordering = ("username",)

    def get_queryset(self):
        queryset = super().get_queryset().order_by("username")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
            )
        return queryset


class UserCreateView(StaffRequiredMixin, BaseCreateView):
    model = get_user_model()
    form_class = forms.AppUserForm
    success_url_name = "prices:user_list"


class UserUpdateView(StaffRequiredMixin, BaseUpdateView):
    model = get_user_model()
    form_class = forms.AppUserForm
    success_url_name = "prices:user_list"


class UserDeleteView(StaffRequiredMixin, BaseDeleteView):
    model = get_user_model()
    success_url_name = "prices:user_list"

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.id == request.user.id:
            messages.error(request, "You cannot delete your own account.")
            return redirect("prices:user_list")
        return super().post(request, *args, **kwargs)


class UserGroupListView(StaffRequiredMixin, BaseListView):
    model = Group
    list_display = ("name",)
    list_title = "User Groups"
    create_url_name = "prices:user_group_create"
    update_url_name = "prices:user_group_update"
    delete_url_name = "prices:user_group_delete"
    detail_url_name = ""
    show_search = True
    ordering = ("name",)

    def get_queryset(self):
        queryset = super().get_queryset().order_by("name")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(name__icontains=query)
        return queryset


class UserGroupCreateView(StaffRequiredMixin, BaseCreateView):
    model = Group
    form_class = forms.AppGroupForm
    success_url_name = "prices:user_group_list"


class UserGroupUpdateView(StaffRequiredMixin, BaseUpdateView):
    model = Group
    form_class = forms.AppGroupForm
    success_url_name = "prices:user_group_list"


class UserGroupDeleteView(StaffRequiredMixin, BaseDeleteView):
    model = Group
    success_url_name = "prices:user_group_list"
