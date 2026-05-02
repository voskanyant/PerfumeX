from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.generic import TemplateView, View

from .services.product_linking import (
    build_product_linking_list_context,
    build_product_linking_search_payload,
    run_product_linking_apply_action,
)


class ProductLinkingView(LoginRequiredMixin, TemplateView):
    template_name = "prices/product_linking.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_product_linking_list_context(self.request))
        return context


class ProductLinkingSearchView(LoginRequiredMixin, View):
    def get(self, request):
        result = build_product_linking_search_payload(request)
        return JsonResponse(result.payload, status=result.status_code)


class ProductLinkingApplyView(LoginRequiredMixin, View):
    def post(self, request):
        return redirect(run_product_linking_apply_action(request.POST))
