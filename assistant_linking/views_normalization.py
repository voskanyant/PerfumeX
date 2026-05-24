from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import DetailView, ListView, TemplateView, View

from assistant_linking import models
from assistant_linking.services.normalization_detail import (
    accept_catalog_candidate,
    build_parsed_product_detail_context,
    lock_supplier_parse,
    normalization_detail_queryset,
    reparse_supplier_product,
    save_brand_alias_for_product,
    save_garbage_keywords_for_product,
    save_product_alias_for_product,
    teach_parse_for_product,
)
from assistant_linking.services.normalization_stats import get_stats_snapshot
from assistant_linking.services.normalization_views import (
    build_atomizer_queryset,
    build_bag_queryset,
    build_complete_parsed_queryset,
    build_cosmetic_queryset,
    build_decant_queryset,
    build_deodorant_queryset,
    build_garbage_queryset,
    build_low_confidence_queryset,
    build_manual_review_queryset,
    build_missing_brand_queryset,
    build_missing_concentration_queryset,
    build_missing_name_queryset,
    build_missing_size_queryset,
    build_modifier_conflict_queryset,
    build_normalization_dashboard_context,
    build_set_queryset,
    build_tester_sample_queryset,
    build_unparsed_queryset,
    build_vintage_queryset,
    dispatch_parse_unparsed_products,
    dispatch_reparse_stale_products,
    dispatch_reparse_visible_products,
    refresh_visible_parsed_context,
    refresh_visible_unparsed_context,
)
from assistant_linking.view_mixins import StaffAssistantMixin
from prices.models import SupplierProduct
from prices.services.pagination import (
    paginate_queryset_by_keyset,
    paginate_queryset_without_count,
)
from prices.services.product_visibility import (
    get_hidden_product_keywords_for_user,
)


def _hidden_product_keywords(request) -> list[str]:
    return get_hidden_product_keywords_for_user(request.user)


class NormalizationDashboardView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_linking/normalization/dashboard.html"

    def get_context_data(self, **kwargs):
        hidden_keywords = _hidden_product_keywords(self.request)
        return {
            **super().get_context_data(**kwargs),
            **build_normalization_dashboard_context(
                self.request,
                hidden_keywords=hidden_keywords,
            ),
        }


class NormalizationSearchMixin:
    search_param = "q"
    search_placeholder = "Search supplier, product, brand, or SKU"
    use_countless_pagination = True

    def get_search_query(self):
        return self.request.GET.get(self.search_param, "").strip()

    def paginate_queryset(self, queryset, page_size):
        if not self.use_countless_pagination:
            return super().paginate_queryset(queryset, page_size)
        page_number = self.kwargs.get(self.page_kwarg) or self.request.GET.get(
            self.page_kwarg
        )
        return paginate_queryset_without_count(
            queryset,
            page_number=page_number,
            page_size=page_size,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = self.get_search_query()
        context["search_placeholder"] = self.search_placeholder
        return context


@method_decorator(never_cache, name="dispatch")
class UnparsedListView(NormalizationSearchMixin, StaffAssistantMixin, ListView):
    model = SupplierProduct
    template_name = "assistant_linking/normalization/product_list.html"
    context_object_name = "products"
    paginate_by = 50
    refresh_param = "refresh"

    def get_queryset(self):
        return build_unparsed_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return refresh_visible_unparsed_context(
            context,
            force_refresh=self.request.GET.get(self.refresh_param) == "1",
            preview=self.request.GET.get("preview") == "1",
        )


class LowConfidenceListView(NormalizationSearchMixin, StaffAssistantMixin, ListView):
    model = models.ParsedSupplierProduct
    template_name = "assistant_linking/normalization/low_confidence.html"
    context_object_name = "parses"
    paginate_by = 50
    refresh_param = "refresh"
    auto_refresh_stale_visible = False
    visible_refresh_parse_saver_kwargs = {}

    def get_queryset(self):
        return build_low_confidence_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )

    def get_refreshed_id_queryset(self, parsed_ids):
        return self.get_queryset().filter(pk__in=parsed_ids)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return refresh_visible_parsed_context(
            context,
            force_refresh=self.request.GET.get(self.refresh_param) == "1",
            auto_refresh_stale=self.auto_refresh_stale_visible,
            parse_saver_kwargs=self.visible_refresh_parse_saver_kwargs,
            parsed_id_queryset_builder=self.get_refreshed_id_queryset,
        )


class NormalizationIssueListView(LowConfidenceListView):
    template_name = "assistant_linking/normalization/issue_list.html"
    issue_title = "Normalisation issues"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "issue_title": self.issue_title}


class MissingBrandListView(NormalizationIssueListView):
    issue_title = "Missing brand"

    def get_queryset(self):
        return build_missing_brand_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class MissingNameListView(NormalizationIssueListView):
    issue_title = "Missing product name"

    def get_queryset(self):
        return build_missing_name_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class MissingConcentrationListView(NormalizationIssueListView):
    issue_title = "Missing concentration"

    def get_queryset(self):
        return build_missing_concentration_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class MissingSizeListView(NormalizationIssueListView):
    issue_title = "Missing or ambiguous size"

    def get_queryset(self):
        return build_missing_size_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class TesterSampleListView(NormalizationIssueListView):
    issue_title = "Tester, sample, and travel rows"

    def get_queryset(self):
        return build_tester_sample_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class SetListView(NormalizationIssueListView):
    issue_title = "Set rows"

    def get_queryset(self):
        return build_set_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class BagListView(NormalizationIssueListView):
    issue_title = "Bag rows"

    def get_queryset(self):
        return build_bag_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class CosmeticListView(NormalizationIssueListView):
    issue_title = "Cosmetics rows"

    def get_queryset(self):
        return build_cosmetic_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class DeodorantListView(NormalizationIssueListView):
    issue_title = "Deodorant rows"

    def get_queryset(self):
        return build_deodorant_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class DecantListView(NormalizationIssueListView):
    issue_title = "Decant rows"

    def get_queryset(self):
        return build_decant_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class VintageListView(NormalizationIssueListView):
    issue_title = "Vintage rows"

    def get_queryset(self):
        return build_vintage_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class AtomizerListView(NormalizationIssueListView):
    issue_title = "Atomizer rows"

    def get_queryset(self):
        return build_atomizer_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class ManualReviewListView(NormalizationIssueListView):
    issue_title = "Manual approval"

    def get_queryset(self):
        return build_manual_review_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class ModifierConflictListView(NormalizationIssueListView):
    issue_title = "Identity modifiers"

    def get_queryset(self):
        return build_modifier_conflict_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class ParsedListView(NormalizationIssueListView):
    issue_title = "Complete parsed products"
    auto_refresh_stale_visible = False
    visible_refresh_parse_saver_kwargs = {
        "apply_catalog_conflicts": False,
        "mark_stats": False,
    }

    def get_cached_total_count(self):
        if self.get_search_query():
            return None
        snapshot = get_stats_snapshot(
            hidden_keywords=_hidden_product_keywords(self.request)
        )
        return snapshot.parsed_count if snapshot else None

    def paginate_queryset(self, queryset, page_size):
        page_number = self.kwargs.get(self.page_kwarg) or self.request.GET.get(
            self.page_kwarg
        )
        return paginate_queryset_by_keyset(
            queryset,
            page_number=page_number,
            page_size=page_size,
            after=self.request.GET.get("after"),
            before=self.request.GET.get("before"),
            first_field="supplier_product_id",
            second_field="pk",
            total_count=self.get_cached_total_count(),
        )

    def get_queryset(self):
        return build_complete_parsed_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class ParseUnparsedProductsView(StaffAssistantMixin, View):
    def post(self, request):
        result = dispatch_parse_unparsed_products()
        getattr(messages, result.message_level)(request, result.message)
        return redirect("assistant_linking:normalization_unparsed")


class RefreshStaleParsesView(StaffAssistantMixin, View):
    def post(self, request):
        result = dispatch_reparse_stale_products()
        getattr(messages, result.message_level)(request, result.message)
        return redirect("assistant_linking:normalization_dashboard")


class RefreshVisibleParsesView(StaffAssistantMixin, View):
    def post(self, request):
        result = dispatch_reparse_visible_products(
            request.POST.getlist("supplier_product_ids")
        )
        getattr(messages, result.message_level)(request, result.message)
        next_url = request.POST.get("next") or ""
        if not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = "assistant_linking:normalization_dashboard"
        return redirect(next_url)


class GarbageListView(NormalizationIssueListView):
    issue_title = "Garbage / excluded rows"

    def get_queryset(self):
        return build_garbage_queryset(
            self.get_search_query(),
            _hidden_product_keywords(self.request),
        )


class ParsedProductDetailView(StaffAssistantMixin, DetailView):
    model = SupplierProduct
    template_name = "assistant_linking/normalization/detail.html"
    context_object_name = "product"
    pk_url_kwarg = "supplier_product_id"

    def get_queryset(self):
        return normalization_detail_queryset()

    def get_context_data(self, **kwargs):
        product = self.object
        return {
            **super().get_context_data(**kwargs),
            **build_parsed_product_detail_context(
                product=product,
                hidden_keywords=_hidden_product_keywords(self.request),
                context_overrides=kwargs,
            ),
        }


def _render_normalization_detail(request, product, **context_overrides):
    view = ParsedProductDetailView()
    view.setup(request, supplier_product_id=product.pk)
    view.object = product
    context = view.get_context_data(object=product, **context_overrides)
    return render(request, view.template_name, context)


class AcceptCatalogCandidateView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        result = accept_catalog_candidate(
            supplier_product_id=supplier_product_id,
            perfume_id=request.POST.get("perfume_id"),
            variant_id=request.POST.get("variant_id"),
            alias_scope=request.POST.get("alias_scope", ""),
            excluded_terms=request.POST.get("excluded_terms", ""),
            user=request.user,
        )
        getattr(messages, result.message_level)(request, result.message)
        return redirect(
            "assistant_linking:normalization_detail",
            supplier_product_id=supplier_product_id,
        )


class ReparseProductView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        result = reparse_supplier_product(
            supplier_product_id=supplier_product_id,
            force=request.POST.get("force") == "1",
        )
        getattr(messages, result.message_level)(request, result.message)
        return redirect(
            "assistant_linking:normalization_detail",
            supplier_product_id=supplier_product_id,
        )


class ExcludeGarbageKeywordView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        result = save_garbage_keywords_for_product(
            supplier_product_id=supplier_product_id,
            keywords_text=request.POST.get("keywords", ""),
            user=request.user,
        )
        getattr(messages, result.message_level)(request, result.message)
        return redirect(
            "assistant_linking:normalization_detail",
            supplier_product_id=supplier_product_id,
        )


class LockParseView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        result = lock_supplier_parse(supplier_product_id=supplier_product_id)
        getattr(messages, result.message_level)(request, result.message)
        return redirect(
            "assistant_linking:normalization_detail",
            supplier_product_id=supplier_product_id,
        )


class SaveBrandAliasView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        result = save_brand_alias_for_product(
            supplier_product_id=supplier_product_id,
            post_data=request.POST,
        )
        getattr(messages, result.message_level)(request, result.message)
        if not result.success:
            return _render_normalization_detail(
                request,
                result.product,
                **{result.form_context_key: result.form},
            )
        return redirect(
            "assistant_linking:normalization_detail",
            supplier_product_id=supplier_product_id,
        )


class SaveProductAliasView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        result = save_product_alias_for_product(
            supplier_product_id=supplier_product_id,
            post_data=request.POST,
        )
        getattr(messages, result.message_level)(request, result.message)
        if not result.success:
            return _render_normalization_detail(
                request,
                result.product,
                **{result.form_context_key: result.form},
            )
        return redirect(
            "assistant_linking:normalization_detail",
            supplier_product_id=supplier_product_id,
        )


class TeachParseView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        result = teach_parse_for_product(
            supplier_product_id=supplier_product_id,
            post_data=request.POST,
            selected_similar_values=request.POST.getlist("selected_similar_ids"),
        )
        getattr(messages, result.message_level)(request, result.message)
        if not result.success:
            return _render_normalization_detail(
                request,
                result.product,
                **{result.form_context_key: result.form},
            )
        return redirect(
            "assistant_linking:normalization_detail",
            supplier_product_id=supplier_product_id,
        )
