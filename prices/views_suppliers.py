from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView

from prices import forms, models
from prices.view_base import BaseCreateView, BaseDeleteView, BaseListView, BaseUpdateView


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
