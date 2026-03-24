from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from collections import defaultdict
import hashlib
import os
import stat
import subprocess
from pathlib import Path
import logging

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
from django.core.paginator import Paginator
from django.core.management import call_command
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from datetime import datetime, time

from decimal import Decimal
import threading
import re
import unicodedata

from django.db.models import (
    Case,
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

from . import forms, models
from django.shortcuts import get_object_or_404, redirect

from .services.importer import delete_import_batch, process_import_file
from django.contrib import messages
from django.db import close_old_connections

from .services.email_importer import run_import
from .services.importer import preview_mapping_file
from .services.cbr_rates import upsert_cbr_markup_rates, upsert_cbr_markup_rates_range


CRON_MARKER = "PERFUMEX_IMPORT_CRON"
PRODUCT_REMOVED_EVENT_PREFIX = "SYSTEM_DEACTIVATE:"
logger = logging.getLogger(__name__)


def _normalize_exclude_terms(raw: str) -> str:
    text = (raw or "").replace(";", "\n").replace(",", "\n")
    terms = []
    seen = set()
    for term in text.splitlines():
        cleaned = term.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(cleaned)
    return "\n".join(terms)


def _parse_exclude_terms(raw: str) -> list[str]:
    normalized = _normalize_exclude_terms(raw)
    return [term.lower() for term in normalized.splitlines() if term.strip()]


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


def _runner_script_path() -> Path:
    base_dir = Path(settings.BASE_DIR)
    return base_dir.parent / "run_import_emails.sh"


def _render_runner_script() -> str:
    base_dir = Path(settings.BASE_DIR)
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            "exec >>/var/log/perfumex_email_import.log 2>&1",
            'echo "=== START $(date \'+%F %T\') ==="',
            f"cd {base_dir}",
            "source .venv/bin/activate",
            "set -a",
            f". {base_dir}/.env",
            "set +a",
            "python manage.py import_emails",
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


def _build_cron_line(script_path: Path) -> str:
    return (
        "*/5 * * * * /usr/bin/flock -n /tmp/perfumex_import.lock "
        f"/usr/bin/timeout 240s /bin/bash {script_path} # {CRON_MARKER}"
    )


def _get_cron_status() -> dict:
    script_path = _runner_script_path()
    try:
        lines = _read_crontab_lines()
        cron_line = next((line for line in lines if CRON_MARKER in line), "")
        return {
            "supported": True,
            "installed": bool(cron_line),
            "line": cron_line,
            "script_path": str(script_path),
            "script_exists": script_path.exists(),
        }
    except Exception as exc:
        return {
            "supported": False,
            "installed": False,
            "line": "",
            "script_path": str(script_path),
            "script_exists": script_path.exists(),
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


class BaseDeleteView(LoginRequiredMixin, DeleteView):
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
        latest_imports = {}
        latest_runs = {}
        batches = (
            models.ImportBatch.objects.select_related("supplier")
            .order_by("-created_at")
        )
        for batch in batches:
            if batch.supplier_id not in latest_imports:
                latest_imports[batch.supplier_id] = batch
        runs = (
            models.EmailImportRun.objects.select_related("supplier")
            .order_by("-started_at")
        )
        for run in runs:
            if run.supplier_id not in latest_runs:
                latest_runs[run.supplier_id] = run
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
            run = latest_runs.get(supplier.id)
            progress = None
            if run and run.total_messages:
                progress = int((run.processed_messages / run.total_messages) * 100)
            rows.append(
                {
                    "supplier": supplier,
                    "latest_import": latest_imports.get(supplier.id),
                    "latest_run": run,
                    "latest_run_progress": progress,
                    "is_running": run and run.status == models.EmailImportStatus.RUNNING,
                }
            )
        context["rows"] = rows
        context["import_batches"] = log_page
        context["import_log_page"] = log_page
        context["any_running"] = models.EmailImportRun.objects.filter(
            status=models.EmailImportStatus.RUNNING
        ).exists()
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
        context["supplier_filter"] = supplier_filter
        context["status_filter"] = status_filter
        context["batch_status_filter"] = batch_status_filter
        context["supplier_options"] = models.Supplier.objects.order_by("name")
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
                script_path = _ensure_runner_script()
                lines = [line for line in _read_crontab_lines() if CRON_MARKER not in line]
                lines.append(_build_cron_line(script_path))
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
            def _run_now():
                close_old_connections()
                call_command("import_emails", force=True)

            thread = threading.Thread(target=_run_now, daemon=True)
            thread.start()
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


class ImportDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        import_batch = get_object_or_404(models.ImportBatch, pk=pk)
        next_url = request.POST.get("next", "").strip()
        delete_import_batch(import_batch)
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return redirect(next_url)
        return redirect("prices:supplier_overview")


class ImportDeleteBulkView(LoginRequiredMixin, View):
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


class SupplierProductCleanupView(LoginRequiredMixin, View):
    def post(self, request):
        models.SupplierProduct.objects.filter(
            created_import_batch__isnull=True, last_import_batch__isnull=True
        ).delete()
        return redirect("prices:product_list")


class SupplierProductInactiveCleanupView(LoginRequiredMixin, View):
    def post(self, request):
        supplier_ids = _parse_supplier_filter_ids(request.POST.get("supplier", ""))
        queryset = models.SupplierProduct.objects.filter(is_active=False)
        if supplier_ids:
            queryset = queryset.filter(supplier_id__in=supplier_ids)
        queryset.delete()
        return redirect("prices:product_list")


class SupplierProductBulkDeleteView(LoginRequiredMixin, View):
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
        queryset = _apply_supplier_product_token_filter(queryset, include_tokens)
        for term in inline_exclude_tokens:
            queryset = queryset.exclude(name__icontains=term)
        if supplier_filter_ids:
            queryset = queryset.filter(supplier_id__in=supplier_filter_ids)
        if status_filter == "active":
            queryset = queryset.filter(is_active=True)
        elif status_filter == "inactive":
            queryset = queryset.filter(is_active=False)
        for term in exclude_terms:
            queryset = queryset.exclude(name__icontains=term)
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
            imported_at = ""
            if product.last_imported_at:
                imported_at = timezone.localtime(product.last_imported_at).strftime(
                    "%d/%m/%Y %H:%M"
                )
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
        for product in visible_products:
            imported_at = ""
            if product.last_imported_at:
                imported_at = timezone.localtime(product.last_imported_at).strftime(
                    "%d/%m/%Y %H:%M"
                )
            items.append(
                {
                    "id": product.id,
                    "supplier": product.supplier.name,
                    "supplier_id": product.supplier_id,
                    "supplier_sku": product.supplier_sku,
                    "name": product.name,
                    "current_price": _format_price(product.display_price, product.display_currency),
                    "last_imported_at": imported_at,
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

    def get_success_url(self):
        return reverse_lazy("prices:supplier_overview")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supplier = get_object_or_404(models.Supplier, pk=self.kwargs["pk"])
        context["supplier"] = supplier
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
        upload = form.cleaned_data["file"]
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
        return super().form_valid(form)


class SupplierEmailImportView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        if not supplier.from_address_pattern:
            messages.info(
                request,
                "Supplier has no sender email configured. Set From address pattern first.",
            )
            return redirect("prices:supplier_overview")
        if models.EmailImportRun.objects.filter(
            supplier=supplier, status=models.EmailImportStatus.RUNNING
        ).exists():
            messages.info(request, "Email import already running for this supplier.")
            return redirect("prices:supplier_overview")
        run = models.EmailImportRun.objects.create(
            supplier=supplier, status=models.EmailImportStatus.RUNNING
        )

        def _run():
            close_old_connections()
            call_command("import_emails", force=True, supplier_id=supplier.id, run_id=run.id)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
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
        if models.EmailImportRun.objects.filter(
            supplier=supplier, status=models.EmailImportStatus.RUNNING
        ).exists():
            messages.info(request, "Email import already running for this supplier.")
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

        def _run():
            close_old_connections()
            mailboxes = models.Mailbox.objects.filter(is_active=True).order_by(
                "priority", "id"
            )
            settings_obj = models.ImportSettings.get_solo()
            timeout_minutes = int(settings_obj.supplier_timeout_minutes or 0)
            timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
            since_date = timezone.make_aware(datetime.combine(start_date, time(0, 0)))
            before_date = None
            if end_date:
                before_date = timezone.make_aware(
                    datetime.combine(end_date + timezone.timedelta(days=1), time(0, 0))
                )
            run_import(
                mailboxes=mailboxes,
                supplier_id=supplier.id,
                mark_seen=False,
                limit=0,
                max_bytes=20_000_000,
                max_seconds=timeout_seconds,
                logger=None,
                run_id=run.id,
                search_criteria="ALL",
                since_date=since_date,
                before_date=before_date,
                from_filter=supplier.from_address_pattern or None,
                subject_filter=supplier.price_subject_pattern or None,
                dedupe_same_day_only=True,
            )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return redirect("prices:supplier_import", pk=pk)


class SupplierEmailBackfillBulkView(LoginRequiredMixin, View):
    def post(self, request):
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

        def _run_bulk():
            close_old_connections()
            mailboxes = list(
                models.Mailbox.objects.filter(is_active=True).order_by("priority", "id")
            )
            settings_obj = models.ImportSettings.get_solo()
            timeout_minutes = int(settings_obj.supplier_timeout_minutes or 0)
            timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
            since_date = timezone.make_aware(datetime.combine(start_date, time(0, 0)))
            before_date = None
            if end_date:
                before_date = timezone.make_aware(
                    datetime.combine(end_date + timezone.timedelta(days=1), time(0, 0))
                )
            for supplier in suppliers:
                if not supplier.from_address_pattern:
                    continue
                if models.EmailImportRun.objects.filter(
                    supplier=supplier, status=models.EmailImportStatus.RUNNING
                ).exists():
                    continue
                run = models.EmailImportRun.objects.create(
                    supplier=supplier,
                    status=models.EmailImportStatus.RUNNING,
                    last_message=f"Bulk backfill {start_date.isoformat()} to {end_date or 'today'}",
                )
                try:
                    run_import(
                        mailboxes=mailboxes,
                        supplier_id=supplier.id,
                        mark_seen=False,
                        limit=0,
                        max_bytes=20_000_000,
                        max_seconds=timeout_seconds,
                        logger=None,
                        run_id=run.id,
                        search_criteria="ALL",
                        since_date=since_date,
                        before_date=before_date,
                        from_filter=supplier.from_address_pattern or None,
                        subject_filter=supplier.price_subject_pattern or None,
                        dedupe_same_day_only=True,
                    )
                except Exception as exc:
                    models.EmailImportRun.objects.filter(id=run.id).update(
                        status=models.EmailImportStatus.FAILED,
                        finished_at=timezone.now(),
                        errors=1,
                        last_message=str(exc),
                    )

        thread = threading.Thread(target=_run_bulk, daemon=True)
        thread.start()
        return redirect("prices:supplier_overview")


class SupplierRatesRecalculateView(LoginRequiredMixin, View):
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


class SupplierEmailImportAllView(LoginRequiredMixin, View):
    def post(self, request):
        if models.EmailImportRun.objects.filter(
            status=models.EmailImportStatus.RUNNING
        ).exists():
            messages.info(request, "Email import already running.")
            return redirect("prices:supplier_overview")
        suppliers = list(
            models.Supplier.objects.filter(
                is_active=True, from_address_pattern__gt=""
            ).order_by("name")
        )
        if not suppliers:
            messages.info(
                request, "No active suppliers with sender email configured."
            )
            return redirect("prices:supplier_overview")

        def _run_all():
            close_old_connections()
            mailboxes = list(
                models.Mailbox.objects.filter(is_active=True).order_by("priority", "id")
            )
            settings_obj = models.ImportSettings.get_solo()
            timeout_minutes = int(settings_obj.supplier_timeout_minutes or 0)
            timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
            for supplier in suppliers:
                run = models.EmailImportRun.objects.create(
                    supplier=supplier, status=models.EmailImportStatus.RUNNING
                )
                since_date = timezone.now() - timezone.timedelta(
                    days=supplier.email_search_days
                )
                try:
                    latest_batch = _get_supplier_latest_batch_time(supplier)
                    if latest_batch and timezone.is_naive(latest_batch):
                        latest_batch = timezone.make_aware(latest_batch)
                    if latest_batch:
                        since_date = timezone.localtime(latest_batch) - timezone.timedelta(days=1)
                    else:
                        since_date = timezone.now() - timezone.timedelta(days=supplier.email_search_days)
                    run_import(
                        mailboxes=mailboxes,
                        supplier_id=supplier.id,
                        mark_seen=True,
                        limit=0,
                        max_bytes=20_000_000,
                        max_seconds=timeout_seconds,
                        logger=None,
                        run_id=run.id,
                        search_criteria="ALL",
                        since_date=since_date,
                        min_received_at=latest_batch,
                        from_filter=supplier.from_address_pattern or None,
                        subject_filter=supplier.price_subject_pattern or None,
                        dedupe_same_day_only=True,
                    )
                except Exception as exc:
                    models.EmailImportRun.objects.filter(id=run.id).update(
                        status=models.EmailImportStatus.FAILED,
                        finished_at=timezone.now(),
                        errors=1,
                        last_message=str(exc),
                    )

        thread = threading.Thread(target=_run_all, daemon=True)
        thread.start()
        return redirect("prices:supplier_overview")


class SupplierPriceReimportAllView(LoginRequiredMixin, View):
    def post(self, request):
        def _run_reimport():
            close_old_connections()
            try:
                call_command("repair_supplier_price_imports", all_suppliers=True)
            except Exception:
                logger.exception("Bulk reimport of all price files failed.")

        thread = threading.Thread(target=_run_reimport, daemon=True)
        thread.start()
        messages.success(
            request,
            "Reimport of all processed price files started in background.",
        )
        return redirect("prices:supplier_overview")


class SupplierEmailImportStatusView(LoginRequiredMixin, View):
    def get(self, request, pk):
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
        runs = (
            models.EmailImportRun.objects.select_related("supplier")
            .order_by("-started_at")
        )
        latest = {}
        for run in runs:
            if run.supplier_id not in latest:
                progress = None
                if run.total_messages:
                    progress = int((run.processed_messages / run.total_messages) * 100)
                detailed_log_tail = (run.detailed_log or "")[-4000:]
                latest[run.supplier_id] = {
                    "status": run.status,
                    "progress": progress,
                    "processed_files": run.processed_files,
                    "errors": run.errors,
                    "last_message": run.last_message,
                    "detailed_log": detailed_log_tail,
                }
        return JsonResponse({"runs": latest})


class SupplierEmailImportCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
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


@method_decorator(csrf_exempt, name="dispatch")
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

            # Long date ranges can exceed request timeouts; run in background.
            def _sync_cbr_range_async(sync_start, sync_end, markup_percent):
                close_old_connections()
                try:
                    upsert_cbr_markup_rates_range(
                        start_date=sync_start,
                        end_date=sync_end,
                        markup_percent=markup_percent,
                    )
                except Exception:
                    # Keep request stable; details can be inspected in server logs.
                    pass

            thread = threading.Thread(
                target=_sync_cbr_range_async,
                args=(start_date, end_date, settings_obj.cbr_markup_percent),
                daemon=True,
            )
            thread.start()
            messages.success(
                request,
                f"CBR range sync started in background: {start_date} to {end_date}.",
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


class CurrencyRateDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        rate = get_object_or_404(models.ExchangeRate, pk=pk)
        rate.delete()
        page = request.POST.get("page", "").strip()
        if page.isdigit():
            return redirect(f"{reverse_lazy('prices:currency_rates')}?page={page}")
        return redirect("prices:currency_rates")


class CurrencyRateBulkDeleteView(LoginRequiredMixin, View):
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
        queryset = _apply_supplier_product_token_filter(queryset, include_tokens)
        for term in inline_exclude_tokens:
            queryset = queryset.exclude(name__icontains=term)
        if supplier_filter_ids:
            queryset = queryset.filter(supplier_id__in=supplier_filter_ids)
        if status_filter == "active":
            queryset = queryset.filter(is_active=True)
        elif status_filter == "inactive":
            queryset = queryset.filter(is_active=False)
        for term in exclude_terms:
            queryset = queryset.exclude(name__icontains=term)
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
        context["exclude_terms"] = getattr(self, "_exclude_terms_raw", _resolve_supplier_exclude_terms(self.request))
        context["price_min"] = getattr(self, "_price_min_raw", self.request.GET.get("price_min", ""))
        context["price_max"] = getattr(self, "_price_max_raw", self.request.GET.get("price_max", ""))
        context["show_currency_filter"] = self.show_currency_filter
        context["show_cleanup"] = True
        context["show_search"] = getattr(self, "show_search", False)
        context["link_detail"] = True
        context["show_status"] = True
        context["show_actions"] = True
        context["show_bulk_delete"] = True
        if currency:
            rates = _get_latest_rates()
            for product in context["object_list"]:
                product.display_currency = currency
                product.display_price = _convert_price(
                    product.current_price, product.currency, currency, rates
                )
            _attach_previous_price_deltas(context["object_list"], currency, rates)
        return context


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


class OurProductListView(BaseListView):
    model = models.OurProduct
    list_display = ("name", "brand", "size", "is_active", "created_at")
    list_title = "Our Products"
    detail_url_name = "prices:our_product_detail"
    create_url_name = "prices:our_product_create"
    update_url_name = "prices:our_product_update"
    delete_url_name = "prices:our_product_delete"


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
        our_products = models.OurProduct.objects.all()
        other_supplier_products = models.SupplierProduct.objects.select_related("supplier").exclude(
            supplier_id=supplier_product.supplier_id
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


