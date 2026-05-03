from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.generic import DetailView, ListView, TemplateView, View

from catalog.models import Perfume as CatalogPerfume
from catalog.models import PerfumeVariant as CatalogPerfumeVariant

from . import forms, models
from .services.catalog_review import (
    build_catalogue_linking_candidate_payload,
    build_catalogue_linking_context,
    build_catalogue_linking_perfume_queryset,
    build_fragrantica_product_review_context,
    build_our_product_detail_context,
    build_our_product_catalog_list_context,
    build_our_product_catalog_variant_queryset,
    run_catalogue_linking_bulk_action,
    run_fragrantica_catalogue_link_action,
    run_catalog_tab_post_action,
    run_catalog_variant_inline_update_action,
)
from .view_base import BaseCreateView, BaseDeleteView, BaseUpdateView


class OurProductListView(LoginRequiredMixin, ListView):
    model = CatalogPerfumeVariant
    template_name = "prices/our_products_catalog.html"
    context_object_name = "variants"
    paginate_by = 50

    def get_queryset(self):
        return build_our_product_catalog_variant_queryset(
            self.request.GET.get("q", "").strip()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_our_product_catalog_list_context(self.request, context))
        return context

    def post(self, request, *args, **kwargs):
        result = run_catalog_tab_post_action(request.POST, host=request.get_host())
        getattr(messages, result.level)(request, result.message)
        return redirect(result.redirect_url)


class FragranticaProductReviewView(LoginRequiredMixin, TemplateView):
    template_name = "prices/fragrantica_products.html"
    paginate_by = 50

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            build_fragrantica_product_review_context(
                self.request,
                page_size=self.paginate_by,
            )
        )
        return context


class FragranticaProductLinkView(LoginRequiredMixin, View):
    def post(self, request, pk):
        result = run_fragrantica_catalogue_link_action(
            pk,
            request.POST,
            host=request.get_host(),
        )
        getattr(messages, result.level)(request, result.message)
        return redirect(result.redirect_url)


class CatalogueLinkingWorkbenchView(LoginRequiredMixin, ListView):
    model = CatalogPerfume
    template_name = "prices/catalogue_linking_workbench.html"
    context_object_name = "perfumes"
    paginate_by = 40

    def get_queryset(self):
        return build_catalogue_linking_perfume_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_catalogue_linking_context(self.request, context))
        return context

    def post(self, request, *args, **kwargs):
        result = run_catalogue_linking_bulk_action(
            request.POST,
            host=request.get_host(),
        )
        getattr(messages, result.level)(request, result.message)
        return redirect(result.redirect_url)


class CatalogueLinkingCandidateView(LoginRequiredMixin, View):
    def get(self, request):
        payload, status_code = build_catalogue_linking_candidate_payload(request)
        return JsonResponse(payload, status=status_code)


class OurProductVariantInlineUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        result = run_catalog_variant_inline_update_action(
            pk,
            request.POST,
            host=request.get_host(),
        )
        getattr(messages, result.level)(request, result.message)
        return redirect(result.redirect_url)


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
        context.update(build_our_product_detail_context(self.request, self.object))
        return context
