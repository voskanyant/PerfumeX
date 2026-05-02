from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from . import models
from .services.autoimport_status import build_autoimport_scan_status
from .services.email_import_lock import email_import_worker_is_busy
from .services.email_import_runs import expire_stale_email_import_runs
from .services.supplier_board import (
    board_sort_key,
    build_supplier_board_row,
    build_supplier_board_summary,
    collect_active_price_mappings,
    collect_latest_attachment_diagnostics,
    collect_latest_failed_import_files,
    collect_latest_runs_and_streaks,
    collect_latest_successful_imports,
)


class SupplierOverviewView(LoginRequiredMixin, TemplateView):
    template_name = "prices/supplier_overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        suppliers = list(models.Supplier.objects.order_by("name"))
        latest_successful_imports = collect_latest_successful_imports()
        latest_failed_import_files = collect_latest_failed_import_files()
        latest_attachment_diagnostics = collect_latest_attachment_diagnostics()
        active_price_mappings = collect_active_price_mappings()
        expire_stale_email_import_runs()
        latest_runs, run_streaks = collect_latest_runs_and_streaks()
        rows = []
        for supplier in suppliers:
            rows.append(
                build_supplier_board_row(
                    supplier=supplier,
                    successful_batch=latest_successful_imports.get(supplier.id),
                    latest_run=latest_runs.get(supplier.id),
                    streak_count=run_streaks.get(supplier.id, 1),
                    latest_failed_file=latest_failed_import_files.get(supplier.id),
                    latest_diagnostic=latest_attachment_diagnostics.get(supplier.id),
                )
            )
            rows[-1]["has_quick_upload"] = supplier.id in active_price_mappings
        rows.sort(key=board_sort_key)
        context["rows"] = rows
        context["supplier_summary"] = build_supplier_board_summary(rows)
        context["autoimport_scan_status"] = build_autoimport_scan_status()
        context["any_running"] = (
            models.EmailImportRun.objects.filter(
                status=models.EmailImportStatus.RUNNING
            ).exists()
            or email_import_worker_is_busy()
        )
        context["import_section"] = "overview"
        context["detailed_logs_url"] = reverse_lazy("prices:import_detailed_logs")
        context["overview_url"] = (
            reverse_lazy("prices:supplier_overview")
            if self.request.user.is_staff
            else reverse_lazy("viewer_import_prices")
        )
        return context
