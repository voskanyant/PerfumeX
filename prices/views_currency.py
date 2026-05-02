from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView, View

from prices import forms, models
from prices.services.cbr_rates import (
    upsert_cbr_markup_rates as _upsert_cbr_markup_rates,
    upsert_cbr_markup_rates_range as _upsert_cbr_markup_rates_range,
)
from prices.view_base import MutatingPermissionRequiredMixin


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
                    usd_rub = _upsert_cbr_markup_rates(
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
                _upsert_cbr_markup_rates_range(
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
