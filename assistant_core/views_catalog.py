from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    RedirectView,
    TemplateView,
    UpdateView,
    View,
)

from assistant_core import forms
from assistant_core.services.catalog_cleanup import (
    build_catalog_cleanup_context,
    merge_catalog_brand,
    merge_catalog_perfume,
)
from assistant_core.services.catalog_import_actions import run_catalog_import_action
from assistant_core.services.catalog_queries import (
    build_catalog_brand_queryset,
    build_catalog_form_context,
    build_catalog_variant_queryset,
)
from assistant_core.view_mixins import StaffAssistantMixin
from catalog.models import Brand, Perfume, PerfumeVariant


class CatalogContextMixin:
    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), **build_catalog_form_context()}


class CatalogBrandListView(StaffAssistantMixin, ListView):
    model = Brand
    template_name = "assistant_core/catalog/brands.html"
    context_object_name = "brands"
    paginate_by = 50

    def get_queryset(self):
        return build_catalog_brand_queryset(self.request.GET.get("q", ""))


class CatalogPerfumeListView(StaffAssistantMixin, RedirectView):
    pattern_name = "prices:our_product_list"
    query_string = True


class CatalogVariantListView(StaffAssistantMixin, ListView):
    model = PerfumeVariant
    template_name = "assistant_core/catalog/variants.html"
    context_object_name = "variants"
    paginate_by = 50

    def get_queryset(self):
        return build_catalog_variant_queryset(self.request.GET.get("q", ""))


class CatalogBrandCreateView(StaffAssistantMixin, CreateView):
    model = Brand
    form_class = forms.CatalogBrandForm
    template_name = "assistant_core/catalog/form.html"
    success_url = reverse_lazy("assistant_core:catalog_brands")


class CatalogBrandUpdateView(CatalogBrandCreateView, UpdateView):
    pass


class CatalogBrandDeleteView(StaffAssistantMixin, DeleteView):
    model = Brand
    template_name = "assistant_core/catalog/confirm_delete.html"
    success_url = reverse_lazy("assistant_core:catalog_brands")


class CatalogPerfumeCreateView(CatalogContextMixin, StaffAssistantMixin, CreateView):
    model = Perfume
    form_class = forms.CatalogPerfumeForm
    template_name = "assistant_core/catalog/form.html"
    success_url = reverse_lazy("assistant_core:catalog_perfumes")


class CatalogPerfumeUpdateView(CatalogPerfumeCreateView, UpdateView):
    pass


class CatalogPerfumeDeleteView(StaffAssistantMixin, DeleteView):
    model = Perfume
    template_name = "assistant_core/catalog/confirm_delete.html"
    success_url = reverse_lazy("assistant_core:catalog_perfumes")


class CatalogVariantCreateView(CatalogContextMixin, StaffAssistantMixin, CreateView):
    model = PerfumeVariant
    form_class = forms.CatalogVariantForm
    template_name = "assistant_core/catalog/form.html"
    success_url = reverse_lazy("assistant_core:catalog_variants")


class CatalogVariantUpdateView(CatalogVariantCreateView, UpdateView):
    pass


class CatalogVariantDeleteView(StaffAssistantMixin, DeleteView):
    model = PerfumeVariant
    template_name = "assistant_core/catalog/confirm_delete.html"
    success_url = reverse_lazy("assistant_core:catalog_variants")


class CatalogImportView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/catalog/import.html"

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "form": kwargs.get("form") or forms.CatalogImportForm(),
        }

    def post(self, request):
        action = run_catalog_import_action(request.POST, request.FILES)
        if action.success:
            messages.success(request, action.message)
        else:
            messages.error(request, action.message)
        return self.render_to_response(
            self.get_context_data(form=action.form, result=action.result)
        )


class CatalogCleanupView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/catalog/cleanup.html"

    def get_context_data(self, **kwargs):
        context = build_catalog_cleanup_context(
            brand_merge_form=kwargs.get("brand_merge_form"),
            perfume_merge_form=kwargs.get("perfume_merge_form"),
        )
        return {**super().get_context_data(**kwargs), **context}


class CatalogBrandMergeView(StaffAssistantMixin, View):
    def post(self, request):
        form = forms.CatalogBrandMergeForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Brand merge was not saved.")
            return render(
                request,
                CatalogCleanupView.template_name,
                build_catalog_cleanup_context(brand_merge_form=form),
            )
        source = form.cleaned_data["source"]
        target = form.cleaned_data["target"]
        merge_catalog_brand(source=source, target=target)
        messages.success(request, f"Merged brand into {target.name}.")
        return redirect("assistant_core:catalog_cleanup")


class CatalogPerfumeMergeView(StaffAssistantMixin, View):
    def post(self, request):
        form = forms.CatalogPerfumeMergeForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Perfume merge was not saved.")
            return render(
                request,
                CatalogCleanupView.template_name,
                build_catalog_cleanup_context(perfume_merge_form=form),
            )
        source = form.cleaned_data["source"]
        target = form.cleaned_data["target"]
        merge_catalog_perfume(source=source, target=target)
        messages.success(request, f"Merged perfume into {target}.")
        return redirect("assistant_core:catalog_cleanup")
