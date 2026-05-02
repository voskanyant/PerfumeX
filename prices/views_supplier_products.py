from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import DetailView, View

from . import forms, models
from .services.currency import get_latest_rates
from .services.product_display import (
    build_supplier_product_detail_context,
    build_supplier_product_list_context,
    build_supplier_product_search_payload,
)
from .services.product_filters import (
    build_supplier_product_queryset_for_request,
    resolve_viewer_front_filter_redirect_url,
    save_front_filters_for_user,
    supplier_product_filter_state_from_request,
    supplier_product_ordering,
)
from .services.product_operations import (
    run_supplier_product_bulk_delete_action,
    run_supplier_product_cleanup_action,
    run_supplier_product_inactive_cleanup_action,
    run_supplier_product_link_action,
)
from .view_base import (
    BaseCreateView,
    BaseDeleteView,
    BaseListView,
    BaseUpdateView,
    MutatingPermissionRequiredMixin,
)


class SupplierProductCleanupView(
    MutatingPermissionRequiredMixin, LoginRequiredMixin, View
):
    permission_required = "prices.delete_supplierproduct"

    def post(self, request):
        redirect_url = run_supplier_product_cleanup_action()
        return redirect(redirect_url)


class SupplierProductInactiveCleanupView(
    MutatingPermissionRequiredMixin, LoginRequiredMixin, View
):
    permission_required = "prices.delete_supplierproduct"

    def post(self, request):
        redirect_url = run_supplier_product_inactive_cleanup_action(
            request.POST.get("supplier", "")
        )
        return redirect(redirect_url)


class SupplierProductBulkDeleteView(
    MutatingPermissionRequiredMixin, LoginRequiredMixin, View
):
    permission_required = "prices.delete_supplierproduct"

    def post(self, request):
        redirect_url = run_supplier_product_bulk_delete_action(
            request.POST.getlist("product_ids"),
            next_url_raw=request.POST.get("next"),
            host=self.request.get_host(),
        )
        return redirect(redirect_url)


class SupplierProductSearchView(LoginRequiredMixin, View):
    def get(self, request):
        return JsonResponse(build_supplier_product_search_payload(request))


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
        return supplier_product_ordering(
            sort_field=self.request.GET.get("sort"),
            sort_dir=self.request.GET.get("dir", "asc"),
            currency=self.request.GET.get("currency", ""),
            status_filter=self.request.GET.get("status", ""),
            allowed_fields=self.list_display,
        )

    def get_queryset(self):
        query_result = build_supplier_product_queryset_for_request(
            self.request,
            rates=get_latest_rates(),
            allowed_fields=self.list_display,
        )
        self._filter_state = query_result.filter_state
        self._price_min_raw = query_result.price_min_raw
        self._price_max_raw = query_result.price_max_raw
        return query_result.queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filter_state = getattr(
            self,
            "_filter_state",
            supplier_product_filter_state_from_request(self.request),
        )
        return build_supplier_product_list_context(
            context,
            filter_state,
            price_min_raw=getattr(
                self,
                "_price_min_raw",
                self.request.GET.get("price_min", ""),
            ),
            price_max_raw=getattr(
                self,
                "_price_max_raw",
                self.request.GET.get("price_max", ""),
            ),
            show_currency_filter=self.show_currency_filter,
            show_cleanup=True,
            show_search=getattr(self, "show_search", False),
            link_detail=getattr(self, "link_detail", False),
            show_status=getattr(self, "show_status", False),
            show_actions=getattr(self, "show_actions", False),
            show_bulk_delete=getattr(self, "show_bulk_delete", False),
            search_url=reverse_lazy("prices:product_search"),
            detail_base_url=reverse_lazy("prices:product_list"),
        )


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
        redirect_url = resolve_viewer_front_filter_redirect_url(request)
        if redirect_url:
            return redirect(redirect_url)
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
            save_front_filters_for_user(request)
        return super().get(request)


class SupplierProductDetailView(LoginRequiredMixin, DetailView):
    model = models.SupplierProduct
    template_name = "prices/product_detail.html"
    detail_fallback_url_name = "prices:product_list"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            build_supplier_product_detail_context(
                self.object,
                self.request.GET,
                link_form_class=forms.SupplierProductLinkForm,
                next_url_raw=self.request.GET.get("next", ""),
                host=self.request.get_host(),
                fallback_url_name=self.detail_fallback_url_name,
            )
        )
        return context


class ViewerProductDetailView(SupplierProductDetailView):
    detail_fallback_url_name = "viewer_home"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["viewer_mode"] = True
        return context


class SupplierProductLinkView(LoginRequiredMixin, View):
    def post(self, request, pk):
        product = get_object_or_404(models.SupplierProduct, pk=pk)
        run_supplier_product_link_action(product, request.POST)
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
