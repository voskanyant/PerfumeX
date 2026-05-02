from __future__ import annotations

from datetime import datetime

from django.core.paginator import Paginator
from django.urls import reverse_lazy
from django.utils import timezone

from prices import models
from prices.services.product_filters import (
    serialize_supplier_filter_ids,
    supplier_filter_ids_from_request,
)


def detailed_log_runs_queryset(*, run_manager=None):
    run_manager = run_manager or models.EmailImportRun.objects
    return run_manager.select_related("supplier").order_by("-started_at")


def detailed_log_batches_queryset(*, batch_manager=None):
    batch_manager = batch_manager or models.ImportBatch.objects
    return (
        batch_manager.select_related("supplier", "mailbox")
        .prefetch_related("importfile_set", "importfile_set__mapping")
        .order_by("-created_at")
    )


def detailed_log_diagnostics_queryset(*, diagnostic_manager=None):
    diagnostic_manager = diagnostic_manager or models.EmailAttachmentDiagnostic.objects
    return diagnostic_manager.select_related(
        "supplier", "mailbox", "import_batch", "import_file"
    ).order_by("-created_at", "-id")


def parse_diagnostic_date_bounds(date_from_raw: str = "", date_to_raw: str = ""):
    try:
        date_from = (
            timezone.make_aware(datetime.fromisoformat(date_from_raw))
            if date_from_raw
            else None
        )
        date_to = (
            timezone.make_aware(datetime.fromisoformat(date_to_raw))
            + timezone.timedelta(days=1)
            if date_to_raw
            else None
        )
    except ValueError:
        return None, None
    return date_from, date_to


def apply_run_filters(runs, *, supplier_filter_ids=None, status_filter=""):
    supplier_filter_ids = supplier_filter_ids or []
    if supplier_filter_ids:
        runs = runs.filter(supplier_id__in=supplier_filter_ids)
    if status_filter:
        runs = runs.filter(status=status_filter)
    return runs


def apply_batch_filters(batches, *, supplier_filter_ids=None, batch_status_filter=""):
    supplier_filter_ids = supplier_filter_ids or []
    if supplier_filter_ids:
        batches = batches.filter(supplier_id__in=supplier_filter_ids)
    if batch_status_filter:
        status_map = {
            "processed": models.ImportStatus.PROCESSED,
            "failed": models.ImportStatus.FAILED,
            "pending": models.ImportStatus.PENDING,
        }
        status = status_map.get(batch_status_filter)
        if status:
            batches = batches.filter(status=status)
    return batches


def apply_diagnostic_filters(
    diagnostics,
    *,
    supplier_filter_ids=None,
    decision_filter="",
    reason_filter="",
    mailbox_filter="",
    filename_filter="",
    sender_filter="",
    date_from_raw="",
    date_to_raw="",
):
    supplier_filter_ids = supplier_filter_ids or []
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
    date_from, date_to = parse_diagnostic_date_bounds(
        date_from_raw=date_from_raw,
        date_to_raw=date_to_raw,
    )
    if date_from:
        diagnostics = diagnostics.filter(created_at__gte=date_from)
    if date_to:
        diagnostics = diagnostics.filter(created_at__lt=date_to)
    return diagnostics


def format_log_time(value) -> str:
    return timezone.localtime(value).strftime("%H:%M:%S") if value else "--:--:--"


def render_batch_console_log(batch) -> str:
    lines = []
    stamp_dt = batch.received_at or batch.created_at
    stamp = format_log_time(stamp_dt)
    mailbox_name = batch.mailbox.name if batch.mailbox else "manual/backfill"
    lines.append(
        f"[{stamp}] BATCH supplier={batch.supplier.name} status={batch.status} mailbox={mailbox_name} "
        f"message_id={batch.message_id or '-'}"
    )
    for file_obj in batch.importfile_set.all():
        file_stamp_dt = file_obj.processed_at or batch.created_at
        file_stamp = format_log_time(file_stamp_dt)
        mapping_name = str(file_obj.mapping) if file_obj.mapping else "-"
        lines.append(
            f"[{file_stamp}] FILE status={file_obj.status} kind={file_obj.file_kind} "
            f"mapping={mapping_name} name='{file_obj.filename}'"
        )
        if file_obj.error_message:
            lines.append(f"[{file_stamp}] ERROR {file_obj.error_message}")
    if batch.error_message:
        lines.append(f"[{stamp}] BATCH_ERROR {batch.error_message}")
    return "\n".join(lines)


def render_run_console_log(run, batch_items, finished_at=None) -> str:
    if run.detailed_log:
        return run.detailed_log
    started = run.started_at
    finished = run.finished_at or finished_at or timezone.now()
    related_batches = [
        batch
        for batch in batch_items
        if batch.supplier_id == run.supplier_id
        and batch.created_at >= started
        and batch.created_at <= finished
    ]
    lines = []
    for batch in related_batches:
        stamp_dt = batch.received_at or batch.created_at
        stamp = format_log_time(stamp_dt)
        mailbox_name = batch.mailbox.name if batch.mailbox else "manual/backfill"
        lines.append(
            f"[{stamp}] BATCH supplier={batch.supplier.name} status={batch.status} mailbox={mailbox_name}"
        )
        for file_obj in batch.importfile_set.all():
            file_stamp_dt = file_obj.processed_at or batch.created_at
            file_stamp = format_log_time(file_stamp_dt)
            mapping_name = str(file_obj.mapping) if file_obj.mapping else "-"
            lines.append(
                f"[{file_stamp}] FILE status={file_obj.status} kind={file_obj.file_kind} "
                f"mapping={mapping_name} name='{file_obj.filename}'"
            )
            if file_obj.error_message:
                lines.append(f"[{file_stamp}] ERROR {file_obj.error_message}")
    return "\n".join(lines)


def build_import_detailed_logs_context(
    request,
    *,
    runs_queryset_func=detailed_log_runs_queryset,
    batches_queryset_func=detailed_log_batches_queryset,
    diagnostics_queryset_func=detailed_log_diagnostics_queryset,
    run_filter_func=apply_run_filters,
    batch_filter_func=apply_batch_filters,
    diagnostic_filter_func=apply_diagnostic_filters,
    render_run_func=render_run_console_log,
    render_batch_func=render_batch_console_log,
    paginator_class=Paginator,
    supplier_options_func=None,
    mailbox_options_func=None,
) -> dict:
    supplier_filter_ids = supplier_filter_ids_from_request(request)
    supplier_filter = serialize_supplier_filter_ids(supplier_filter_ids)
    status_filter = request.GET.get("run_status", "").strip()
    batch_status_filter = request.GET.get("batch_status", "").strip()
    decision_filter = request.GET.get("decision", "").strip()
    reason_filter = request.GET.get("reason", "").strip()
    mailbox_filter = request.GET.get("mailbox", "").strip()
    filename_filter = request.GET.get("filename", "").strip()
    sender_filter = request.GET.get("sender", "").strip()
    date_from_raw = request.GET.get("date_from", "").strip()
    date_to_raw = request.GET.get("date_to", "").strip()

    runs = run_filter_func(
        runs_queryset_func(),
        supplier_filter_ids=supplier_filter_ids,
        status_filter=status_filter,
    )
    runs_page = paginator_class(runs, 30).get_page(request.GET.get("page", "1"))
    run_items = list(runs_page.object_list)

    batches = batch_filter_func(
        batches_queryset_func(),
        supplier_filter_ids=supplier_filter_ids,
        batch_status_filter=batch_status_filter,
    )
    batches_page = paginator_class(batches, 20).get_page(request.GET.get("bpage", "1"))
    batch_items = list(batches_page.object_list)

    diagnostics = diagnostic_filter_func(
        diagnostics_queryset_func(),
        supplier_filter_ids=supplier_filter_ids,
        decision_filter=decision_filter,
        reason_filter=reason_filter,
        mailbox_filter=mailbox_filter,
        filename_filter=filename_filter,
        sender_filter=sender_filter,
        date_from_raw=date_from_raw,
        date_to_raw=date_to_raw,
    )
    diagnostics_page = paginator_class(diagnostics, 40).get_page(
        request.GET.get("dpage", "1")
    )

    for run in run_items:
        run.console_log = render_run_func(run, batch_items)

    for batch in batch_items:
        batch.console_log = render_batch_func(batch)

    runs_page.object_list = run_items
    batches_page.object_list = batch_items

    supplier_options = (
        supplier_options_func()
        if supplier_options_func
        else models.Supplier.objects.order_by("name")
    )
    mailbox_options = (
        mailbox_options_func()
        if mailbox_options_func
        else models.Mailbox.objects.order_by("name")
    )

    return {
        "runs_page": runs_page,
        "batches_page": batches_page,
        "diagnostics_page": diagnostics_page,
        "supplier_filter": supplier_filter,
        "status_filter": status_filter,
        "batch_status_filter": batch_status_filter,
        "decision_filter": decision_filter,
        "reason_filter": reason_filter,
        "mailbox_filter": mailbox_filter,
        "filename_filter": filename_filter,
        "sender_filter": sender_filter,
        "date_from": date_from_raw,
        "date_to": date_to_raw,
        "supplier_options": supplier_options,
        "mailbox_options": mailbox_options,
        "decision_options": models.AttachmentDecision.choices,
        "reason_options": models.AttachmentReason.choices,
        "run_status_options": [
            models.EmailImportStatus.RUNNING,
            models.EmailImportStatus.FINISHED,
            models.EmailImportStatus.FAILED,
            models.EmailImportStatus.CANCELED,
        ],
        "batch_status_options": [
            ("processed", "Processed"),
            ("failed", "Failed"),
            ("pending", "Pending"),
        ],
        "import_section": "detailed_logs",
        "detailed_logs_url": reverse_lazy("prices:import_detailed_logs"),
        "overview_url": reverse_lazy("prices:supplier_overview"),
    }
