from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from django.views.generic import DetailView, FormView, View
from django.views.decorators.http import require_POST

from . import forms, models
from .services.currency import run_supplier_rates_recalculation_action
from .services.email_import_runs import (
    build_stuck_email_import_runs_context,
    cancel_supplier_email_import,
    recover_stuck_email_import_run,
    run_bulk_email_backfill_action,
    run_supplier_board_mailbox_scan_action,
    run_supplier_email_backfill_action,
    run_supplier_email_import_action,
    supplier_email_import_status_payload,
)
from .services.import_operations import (
    build_import_wizard_initial,
    build_supplier_mapping_preview_result,
    build_supplier_import_context,
    build_supplier_import_initial,
    import_board_redirect_url,
    import_settings_or_overview_redirect_name,
    process_import_wizard_upload,
    run_import_delete_bulk_action,
    run_bulk_price_reimport_action,
    run_import_delete_action,
    run_supplier_import_form_action,
    run_supplier_quick_upload_action,
    run_supplier_price_source_create_action,
    run_supplier_price_source_delete_action,
    run_supplier_price_source_import_action,
    supplier_import_tab_url,
)
from .services.import_logs import (
    build_import_detailed_logs_context,
)
from .services.import_history import build_import_detail_context
from .services.import_scheduler import (
    build_import_settings_context,
    run_import_settings_post_action,
)
from .services.supplier_board import (
    supplier_email_import_status_all_payload,
)
from .view_base import MutatingPermissionRequiredMixin


def _add_action_message(request, result):
    message_func = getattr(messages, result.message_level)
    message_func(request, result.message)


def _import_board_redirect(request):
    return redirect(
        import_board_redirect_url(
            next_url_raw=request.POST.get("next", ""),
            host=request.get_host(),
            is_staff=request.user.is_staff,
        )
    )


class ImportDetailedLogsView(LoginRequiredMixin, TemplateView):
    template_name = "prices/import_detailed_logs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_import_detailed_logs_context(self.request))
        return context


class StuckEmailImportRunsView(LoginRequiredMixin, TemplateView):
    template_name = "prices/stuck_email_import_runs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_stuck_email_import_runs_context())
        return context

    def post(self, request, *args, **kwargs):
        result = recover_stuck_email_import_run(request.POST.get("run_id", ""))
        _add_action_message(request, result)
        return redirect("prices:stuck_email_import_runs")


class ImportSettingsView(LoginRequiredMixin, TemplateView):
    template_name = "prices/import_settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_import_settings_context())
        return context

    def post(self, request, *args, **kwargs):
        result = run_import_settings_post_action(request.POST)
        _add_action_message(request, result)
        return redirect("prices:import_settings")


class ImportWizardView(LoginRequiredMixin, FormView):
    template_name = "prices/import_wizard.html"
    form_class = forms.ImportWizardForm
    success_url = reverse_lazy("prices:supplier_overview")

    def get_initial(self):
        return build_import_wizard_initial(
            initial=super().get_initial(),
            supplier_raw=self.request.GET.get("supplier"),
            file_kind_raw=self.request.GET.get("file_kind"),
        )

    def form_valid(self, form):
        supplier = form.cleaned_data["supplier"]
        file_kind = form.cleaned_data["file_kind"]
        upload = form.cleaned_data["file"]

        process_import_wizard_upload(supplier, file_kind, upload)
        return super().form_valid(form)


class ImportDeleteView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_importbatch"

    def post(self, request, pk):
        import_batch = get_object_or_404(models.ImportBatch, pk=pk)
        redirect_url = run_import_delete_action(
            import_batch,
            next_url_raw=request.POST.get("next", ""),
            host=self.request.get_host(),
        )
        return redirect(redirect_url)


class ImportDeleteBulkView(MutatingPermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = "prices.delete_importbatch"

    def post(self, request):
        redirect_url = run_import_delete_bulk_action(request.POST.getlist("import_ids"))
        return redirect(redirect_url)


class ImportDetailView(LoginRequiredMixin, DetailView):
    model = models.ImportBatch
    template_name = "prices/import_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_import_detail_context(self.object, self.request))
        return context


class SupplierImportView(LoginRequiredMixin, FormView):
    template_name = "prices/supplier_import.html"
    form_class = forms.SupplierImportForm

    def get_success_url(self):
        return reverse_lazy("prices:supplier_overview")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supplier = get_object_or_404(models.Supplier, pk=self.kwargs["pk"])
        context.update(
            build_supplier_import_context(
                supplier,
                source_raw=self.request.GET.get("source", "email"),
            )
        )
        return context

    def get_initial(self):
        supplier = get_object_or_404(models.Supplier, pk=self.kwargs["pk"])
        return build_supplier_import_initial(
            supplier,
            initial=super().get_initial(),
        )

    def form_valid(self, form):
        supplier = get_object_or_404(models.Supplier, pk=self.kwargs["pk"])
        result = run_supplier_import_form_action(
            supplier,
            form,
            action_raw=self.request.POST.get("action", "upload_import"),
            source_raw=self.request.GET.get("source", "file"),
        )
        if result.form_error_field:
            form.add_error(result.form_error_field, result.form_error_message)
            return self.form_invalid(form)
        if result.action:
            _add_action_message(self.request, result.action)
        if result.redirect_url:
            return redirect(result.redirect_url)
        return super().form_valid(form)


class SupplierQuickUploadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        result = run_supplier_quick_upload_action(
            supplier,
            request.FILES.get("file"),
        )
        _add_action_message(request, result)
        if result.redirect_source == "file" and request.user.is_staff:
            return redirect(supplier_import_tab_url(pk, "file"))
        return _import_board_redirect(request)


class SupplierPriceSourceCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        form = forms.SupplierPriceSourceForm(request.POST)
        result = run_supplier_price_source_create_action(supplier, form)
        _add_action_message(request, result)
        return redirect(supplier_import_tab_url(pk, "link"))


class SupplierPriceSourceImportView(LoginRequiredMixin, View):
    def post(self, request, pk, source_pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        source = get_object_or_404(
            models.SupplierPriceSource, pk=source_pk, supplier=supplier
        )
        result = run_supplier_price_source_import_action(supplier, source)
        _add_action_message(request, result)
        return redirect(supplier_import_tab_url(pk, result.redirect_source or "link"))


class SupplierPriceSourceDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, source_pk):
        source = get_object_or_404(
            models.SupplierPriceSource, pk=source_pk, supplier_id=pk
        )
        result = run_supplier_price_source_delete_action(source)
        _add_action_message(request, result)
        return redirect(supplier_import_tab_url(pk, "link"))


class SupplierEmailImportView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        result = run_supplier_email_import_action(supplier)
        if result.message_level:
            _add_action_message(request, result)
        return _import_board_redirect(request)


class SupplierEmailBackfillView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        result = run_supplier_email_backfill_action(
            supplier,
            start_raw=request.POST.get("start_date", ""),
            end_raw=request.POST.get("end_date", ""),
        )
        if result.message_level:
            _add_action_message(request, result)
        return redirect("prices:supplier_import", pk=pk)


class SupplierEmailBackfillBulkView(
    MutatingPermissionRequiredMixin, LoginRequiredMixin, View
):
    permission_required = "prices.add_emailimportrun"

    def post(self, request):
        redirect_name = import_settings_or_overview_redirect_name(
            request.POST.get("next")
        )
        result = run_bulk_email_backfill_action(
            request.POST.getlist("supplier_ids"),
            start_raw=request.POST.get("start_date", ""),
            end_raw=request.POST.get("end_date", ""),
        )
        if result.message_level:
            _add_action_message(request, result)
        return redirect(redirect_name)


class SupplierRatesRecalculateView(
    MutatingPermissionRequiredMixin, LoginRequiredMixin, View
):
    permission_required = "prices.change_exchangerate"

    def post(self, request):
        redirect_name = import_settings_or_overview_redirect_name(
            request.POST.get("next")
        )
        result = run_supplier_rates_recalculation_action(
            request.POST.getlist("supplier_ids"),
            start_raw=request.POST.get("start_date", ""),
            end_raw=request.POST.get("end_date", ""),
        )
        _add_action_message(request, result)
        return redirect(redirect_name)


class SupplierEmailImportAllView(LoginRequiredMixin, View):
    def post(self, request):
        result = run_supplier_board_mailbox_scan_action()
        _add_action_message(request, result)
        return _import_board_redirect(request)


class SupplierPriceReimportAllView(
    MutatingPermissionRequiredMixin, LoginRequiredMixin, View
):
    permission_required = "prices.change_importbatch"

    def post(self, request):
        result = run_bulk_price_reimport_action()
        _add_action_message(request, result)
        return redirect("prices:supplier_overview")


class SupplierEmailImportStatusView(LoginRequiredMixin, View):
    def get(self, request, pk):
        return JsonResponse(supplier_email_import_status_payload(pk))


class SupplierEmailImportStatusAllView(LoginRequiredMixin, View):
    def get(self, request):
        return JsonResponse(supplier_email_import_status_all_payload())


class SupplierEmailImportCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(models.Supplier, pk=pk)
        result = cancel_supplier_email_import(supplier)
        _add_action_message(request, result)
        return redirect("prices:supplier_overview")


@method_decorator(require_POST, name="dispatch")
class SupplierMappingPreviewView(LoginRequiredMixin, View):
    def post(self, request, pk):
        result = build_supplier_mapping_preview_result(request.FILES, request.POST)
        return JsonResponse(result.payload, status=result.status)
