from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404, HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView, View

from assistant_linking import forms, models
from assistant_linking.services.catalog_matcher import candidate_matches, rule_impact, similar_supplier_rows
from assistant_linking.services.garbage import GARBAGE_MODIFIER, normalize_garbage_keyword
from assistant_linking.services.grouping import rebuild_groups
from assistant_linking.services.mock_suggester import generate_link_suggestions
from assistant_linking.services.normalization_stats import (
    complete_parse_query,
    empty_stats,
    get_stats_snapshot,
    refresh_stats_snapshot,
    snapshot_to_stats,
)
from assistant_linking.services.normalizer import save_parse
from assistant_linking.services.smart_search import normalize_query
from catalog.models import Brand, Perfume, PerfumeVariant, compact_decimal_text
from prices.models import SupplierProduct
from prices.services.product_visibility import (
    apply_hidden_product_keywords,
    get_hidden_product_keywords_for_user,
)


BULK_LINK_PRODUCT_CAP = 200
UNDO_WINDOW_SECONDS = 30
SUPPLIER_PRODUCT_HIDDEN_FIELDS = ("name", "brand", "supplier_sku")
PARSED_PRODUCT_HIDDEN_FIELDS = (
    "supplier_product__name",
    "supplier_product__brand",
    "supplier_product__supplier_sku",
)

def _hidden_product_keywords(request) -> list[str]:
    return get_hidden_product_keywords_for_user(request.user)


def _hide_supplier_products(queryset, request):
    return apply_hidden_product_keywords(
        queryset,
        _hidden_product_keywords(request),
        fields=SUPPLIER_PRODUCT_HIDDEN_FIELDS,
    )


def _hide_parsed_products(queryset, request):
    return apply_hidden_product_keywords(
        queryset,
        _hidden_product_keywords(request),
        fields=PARSED_PRODUCT_HIDDEN_FIELDS,
    )


def _exclude_garbage_parses(queryset):
    return queryset.exclude(modifiers__contains=[GARBAGE_MODIFIER])


def _exclude_set_parses(queryset):
    return queryset.exclude(is_set=True)


def _complete_parse_query():
    return complete_parse_query()


def _complete_parses(queryset):
    return queryset.filter(_complete_parse_query())


def _normalization_dashboard_stats(request, hidden_keywords: list[str]) -> dict[str, object]:
    if request.GET.get("refresh") == "1":
        return snapshot_to_stats(refresh_stats_snapshot(hidden_keywords=hidden_keywords))
    snapshot = get_stats_snapshot(hidden_keywords=hidden_keywords)
    return snapshot_to_stats(snapshot) if snapshot else empty_stats()


def _apply_parsed_search(queryset, query):
    if not query:
        return queryset
    return queryset.filter(
        Q(supplier_product__supplier__name__icontains=query)
        | Q(supplier_product__name__icontains=query)
        | Q(supplier_product__supplier_sku__icontains=query)
        | Q(supplier_product__brand__icontains=query)
        | Q(normalized_brand__name__icontains=query)
        | Q(detected_brand_text__icontains=query)
        | Q(product_name_text__icontains=query)
        | Q(concentration__icontains=query)
    )


def _manual_decision_snapshot(decision):
    return {
        "id": decision.id,
        "supplier_product_id": decision.supplier_product_id,
        "perfume_id": decision.perfume_id,
        "variant_id": decision.variant_id,
        "decision_type": decision.decision_type,
        "reason": decision.reason,
        "apply_to_similar": decision.apply_to_similar,
        "created_by_id": decision.created_by_id,
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
    }


def _record_manual_link_decision(*, supplier_product, perfume_id, variant_id, decision_type, reason, apply_to_similar, created_by, allow_overwrite=False):
    previous = None
    if allow_overwrite:
        previous = (
            models.ManualLinkDecision.objects.select_for_update()
            .filter(supplier_product=supplier_product)
            .order_by("-created_at", "-id")
            .first()
        )
    decision = models.ManualLinkDecision.objects.create(
        supplier_product=supplier_product,
        perfume_id=perfume_id or None,
        variant_id=variant_id or None,
        decision_type=decision_type,
        reason=reason,
        apply_to_similar=apply_to_similar,
        created_by=created_by,
    )
    if previous:
        models.ManualLinkDecisionAudit.objects.create(
            previous_pk=previous.pk,
            previous_decision_json=_manual_decision_snapshot(previous),
            replaced_by=decision,
        )
    return decision


def _prune_link_actions(user):
    stale_ids = list(
        models.LinkAction.objects.filter(user=user)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)[50:]
    )
    if stale_ids:
        models.LinkAction.objects.filter(id__in=stale_ids).delete()


def _latest_undoable_action(user):
    cutoff = timezone.now() - timedelta(seconds=UNDO_WINDOW_SECONDS)
    return (
        models.LinkAction.objects.filter(
            user=user,
            action_type=models.LinkAction.ACTION_BULK_LINK,
            created_at__gte=cutoff,
        )
        .exclude(payload_json__status="UNDONE")
        .order_by("-created_at", "-id")
        .first()
    )


def _bulk_link_products(*, user, product_ids, perfume_id, variant_id, allow_overwrite, apply_to_similar, reason):
    payload_items = []
    linked = 0
    skipped = 0
    with transaction.atomic():
        products = list(
            SupplierProduct.objects.select_for_update()
            .filter(id__in=product_ids)
            .order_by("id")
        )
        for product in products:
            had_link = bool(product.catalog_perfume_id or product.catalog_variant_id)
            previous = {
                "product_id": product.id,
                "catalog_perfume_id": product.catalog_perfume_id,
                "catalog_variant_id": product.catalog_variant_id,
            }
            if had_link and not allow_overwrite:
                skipped += 1
                payload_items.append({**previous, "linked": False, "skipped": True})
                continue
            product.catalog_perfume_id = perfume_id or None
            product.catalog_variant_id = variant_id or None
            product.save(update_fields=["catalog_perfume", "catalog_variant", "updated_at"])
            _record_manual_link_decision(
                supplier_product=product,
                perfume_id=perfume_id or None,
                variant_id=variant_id or None,
                decision_type=models.ManualLinkDecision.DECISION_APPROVE_VARIANT if variant_id else models.ManualLinkDecision.DECISION_APPROVE_PERFUME,
                reason=reason,
                apply_to_similar=apply_to_similar or len(product_ids) > 1,
                created_by=user,
                allow_overwrite=allow_overwrite and had_link,
            )
            linked += 1
            payload_items.append(
                {
                    **previous,
                    "linked": True,
                    "skipped": False,
                    "new_catalog_perfume_id": perfume_id or None,
                    "new_catalog_variant_id": variant_id or None,
                }
            )
    action = models.LinkAction.objects.create(
        user=user,
        action_type=models.LinkAction.ACTION_BULK_LINK,
        payload_json={
            "status": "COMPLETE",
            "matched": len(product_ids),
            "linked": linked,
            "skipped": skipped,
            "items": payload_items,
        },
    )
    _prune_link_actions(user)
    return action


def _undo_link_action(action, user):
    payload = action.payload_json or {}
    items = payload.get("items") or []
    restored = 0
    with transaction.atomic():
        for item in items:
            if not item.get("linked"):
                continue
            product = SupplierProduct.objects.select_for_update().get(pk=item["product_id"])
            product.catalog_perfume_id = item.get("catalog_perfume_id")
            product.catalog_variant_id = item.get("catalog_variant_id")
            product.save(update_fields=["catalog_perfume", "catalog_variant", "updated_at"])
            restored += 1
        models.LinkAction.objects.create(
            user=user,
            action_type=models.LinkAction.ACTION_UNDO_BULK_LINK,
            payload_json={"undone_action_id": action.id, "restored": restored},
        )
        action.payload_json = {**payload, "status": "UNDONE", "undone_at": timezone.now().isoformat()}
        action.save(update_fields=["payload_json"])
    _prune_link_actions(user)
    return restored


class StaffAssistantMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return bool(self.request.user and self.request.user.is_staff)


class NormalizationDashboardView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_linking/normalization/dashboard.html"

    def get_context_data(self, **kwargs):
        hidden_keywords = _hidden_product_keywords(self.request)
        stats = _normalization_dashboard_stats(self.request, hidden_keywords)
        recent_ids = stats.get("recent_ids") or []
        recent = (
            models.ParsedSupplierProduct.objects.select_related("supplier_product", "normalized_brand")
            .filter(id__in=recent_ids)
            .order_by("-updated_at")[:20]
        )
        return {
            **super().get_context_data(**kwargs),
            **{key: value for key, value in stats.items() if key != "recent_ids"},
            "recent": recent,
            "hidden_keywords_active": bool(hidden_keywords),
        }


class NormalizationSearchMixin:
    search_param = "q"
    search_placeholder = "Search supplier, product, brand, or SKU"

    def get_search_query(self):
        return self.request.GET.get(self.search_param, "").strip()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = self.get_search_query()
        context["search_placeholder"] = self.search_placeholder
        return context


class UnparsedListView(NormalizationSearchMixin, StaffAssistantMixin, ListView):
    model = SupplierProduct
    template_name = "assistant_linking/normalization/product_list.html"
    context_object_name = "products"
    paginate_by = 50

    def get_queryset(self):
        queryset = SupplierProduct.objects.select_related("supplier").filter(assistant_parse__isnull=True)
        query = self.get_search_query()
        if query:
            queryset = queryset.filter(
                Q(supplier__name__icontains=query)
                | Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(size__icontains=query)
                | Q(supplier_sku__icontains=query)
            )
        queryset = _hide_supplier_products(queryset, self.request)
        return queryset.order_by("supplier__name", "name")


class LowConfidenceListView(NormalizationSearchMixin, StaffAssistantMixin, ListView):
    model = models.ParsedSupplierProduct
    template_name = "assistant_linking/normalization/low_confidence.html"
    context_object_name = "parses"
    paginate_by = 50

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(confidence__lt=75)
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _exclude_set_parses(_exclude_garbage_parses(_hide_parsed_products(queryset, self.request)))
        return queryset.order_by("confidence", "supplier_product__supplier__name", "supplier_product__name")


class NormalizationIssueListView(LowConfidenceListView):
    template_name = "assistant_linking/normalization/issue_list.html"
    issue_title = "Normalisation issues"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "issue_title": self.issue_title}


class MissingBrandListView(NormalizationIssueListView):
    issue_title = "Missing brand"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(normalized_brand__isnull=True)
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _exclude_set_parses(_exclude_garbage_parses(_hide_parsed_products(queryset, self.request)))
        return queryset.order_by("supplier_product__name")


class MissingNameListView(NormalizationIssueListView):
    issue_title = "Missing product name"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(product_name_text="")
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _exclude_set_parses(_exclude_garbage_parses(_hide_parsed_products(queryset, self.request)))
        return queryset.order_by("supplier_product__name")


class MissingConcentrationListView(NormalizationIssueListView):
    issue_title = "Missing concentration"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(concentration="")
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _exclude_set_parses(_exclude_garbage_parses(_hide_parsed_products(queryset, self.request)))
        return queryset.order_by("supplier_product__name")


class MissingSizeListView(NormalizationIssueListView):
    issue_title = "Missing or ambiguous size"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(size_ml__isnull=True)
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _exclude_set_parses(_exclude_garbage_parses(_hide_parsed_products(queryset, self.request)))
        return queryset.order_by("supplier_product__name")


class TesterSampleListView(NormalizationIssueListView):
    issue_title = "Tester, sample, and travel rows"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(Q(is_tester=True) | Q(is_sample=True) | Q(is_travel=True), is_set=False)
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _exclude_garbage_parses(_hide_parsed_products(queryset, self.request))
        return queryset.order_by("supplier_product__name")


class SetListView(NormalizationIssueListView):
    issue_title = "Set rows"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(is_set=True)
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _exclude_garbage_parses(_hide_parsed_products(queryset, self.request))
        return queryset.order_by("supplier_product__supplier__name", "supplier_product__name")


class ModifierConflictListView(NormalizationIssueListView):
    issue_title = "Identity modifiers"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).exclude(modifiers=[])
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _exclude_set_parses(_exclude_garbage_parses(_hide_parsed_products(queryset, self.request)))
        return queryset.order_by("supplier_product__name")


class ParsedListView(NormalizationIssueListView):
    issue_title = "Complete parsed products"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        )
        queryset = _hide_parsed_products(queryset, self.request)
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _complete_parses(_exclude_garbage_parses(queryset))
        return queryset.order_by("-updated_at", "supplier_product__supplier__name", "supplier_product__name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        refreshed_parses = [
            save_parse(parsed.supplier_product)
            for parsed in context.get("parses", [])
        ]
        context["parses"] = refreshed_parses
        context["object_list"] = refreshed_parses
        if context.get("page_obj"):
            context["page_obj"].object_list = refreshed_parses
        return context


class GarbageListView(NormalizationIssueListView):
    issue_title = "Garbage / excluded rows"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(modifiers__contains=[GARBAGE_MODIFIER])
        queryset = _apply_parsed_search(queryset, self.get_search_query())
        queryset = _hide_parsed_products(queryset, self.request)
        return queryset.order_by("supplier_product__supplier__name", "supplier_product__name")


class ParsedProductDetailView(StaffAssistantMixin, DetailView):
    model = SupplierProduct
    template_name = "assistant_linking/normalization/detail.html"
    context_object_name = "product"
    pk_url_kwarg = "supplier_product_id"

    def get_queryset(self):
        return SupplierProduct.objects.select_related(
            "supplier",
            "catalog_perfume__brand",
            "catalog_variant",
        )

    def get_context_data(self, **kwargs):
        product = self.object
        parsed = save_parse(product)
        canonical_perfume = product.catalog_perfume
        canonical_variant = product.catalog_variant
        is_garbage = GARBAGE_MODIFIER in (parsed.modifiers or [])
        catalog_candidates = [] if is_garbage or parsed.is_set else candidate_matches(parsed)
        suggested_candidate = None
        if not canonical_perfume and catalog_candidates:
            best_candidate = catalog_candidates[0]
            if best_candidate.score >= 80 and "concentration differs" in best_candidate.conflicts:
                suggested_candidate = best_candidate
        product_alias_text = parsed.product_name_text or product.name
        brand_alias_text = parsed.detected_brand_text or product.brand
        existing_alias = None
        if parsed.normalized_brand_id and product_alias_text:
            alias_queryset = models.ProductAlias.objects.filter(
                brand=parsed.normalized_brand,
                active=True,
            ).filter(
                Q(alias_text__iexact=product_alias_text) | Q(canonical_text__iexact=product_alias_text),
                Q(supplier=product.supplier) | Q(supplier__isnull=True),
            )
            existing_alias = alias_queryset.order_by("supplier_id", "priority").first()
        existing_blockers = existing_alias.excluded_terms if existing_alias else ""
        teaching_perfume = canonical_perfume or (suggested_candidate.perfume if suggested_candidate else None)
        teaching_variant = canonical_variant or (suggested_candidate.variant if suggested_candidate else None)
        teach_initial = {
            "supplier_brand_text": brand_alias_text,
            "brand_name": teaching_perfume.brand.name if teaching_perfume else (parsed.normalized_brand.name if parsed.normalized_brand_id else parsed.detected_brand_text),
            "supplier_product_text": product_alias_text,
            "product_name": teaching_perfume.name if teaching_perfume else parsed.product_name_text,
            "product_excluded_terms": existing_blockers,
            "supplier_concentration_text": parsed.concentration,
            "concentration": teaching_perfume.concentration if teaching_perfume else parsed.concentration,
            "supplier_size_text": parsed.raw_size_text or product.size,
            "size_ml": (
                compact_decimal_text(teaching_variant.size_ml)
                if teaching_variant and teaching_variant.size_ml
                else compact_decimal_text(parsed.size_ml)
                if parsed.size_ml
                else None
            ),
            "supplier_audience_text": parsed.supplier_gender_hint,
            "audience": teaching_perfume.audience if teaching_perfume and teaching_perfume.audience else parsed.supplier_gender_hint,
            "supplier_type_text": parsed.variant_type,
            "variant_type": teaching_variant.variant_type if teaching_variant and teaching_variant.variant_type else parsed.variant_type,
            "supplier_packaging_text": parsed.packaging,
            "packaging": teaching_variant.packaging if teaching_variant and teaching_variant.packaging else parsed.packaging,
            "alias_scope": forms.ParseTeachingForm.SCOPE_GLOBAL,
            "lock_parse": True,
            "reparse_similar": False,
        }
        return {
            **super().get_context_data(**kwargs),
            "parsed": parsed,
            "teach_form": kwargs.get("teach_form") or forms.ParseTeachingForm(initial=teach_initial),
            "brand_alias_form": kwargs.get("brand_alias_form"),
            "product_alias_form": kwargs.get("product_alias_form"),
            "catalog_candidates": catalog_candidates,
            "is_garbage": is_garbage,
            "suggested_catalog_candidate": suggested_candidate,
            "similar_rows": similar_supplier_rows(
                product,
                parsed,
                hidden_terms=_hidden_product_keywords(self.request),
            ),
            "rule_impact": rule_impact(
                product,
                brand_alias_text,
                product_alias_text,
                existing_blockers,
                hidden_terms=_hidden_product_keywords(self.request),
            ),
            "catalog_brands": Brand.objects.filter(is_active=True).order_by("name")[:1000],
            "catalog_perfumes": Perfume.objects.select_related("brand").order_by("brand__name", "name")[:2000],
            "catalog_packagings": PerfumeVariant.objects.exclude(packaging="").values_list("packaging", flat=True).distinct().order_by("packaging"),
            "catalog_variant_types": PerfumeVariant.objects.exclude(variant_type="").values_list("variant_type", flat=True).distinct().order_by("variant_type"),
        }


def _mark_invalid_fields_for_a11y(form):
    for field_name in form.errors:
        if field_name in form.fields:
            form.fields[field_name].widget.attrs["aria-describedby"] = f"id_{field_name}_errors"
            form.fields[field_name].widget.attrs["aria-invalid"] = "true"


def _render_normalization_detail(request, product, **context_overrides):
    view = ParsedProductDetailView()
    view.setup(request, supplier_product_id=product.pk)
    view.object = product
    context = view.get_context_data(object=product, **context_overrides)
    return render(request, view.template_name, context)


class AcceptCatalogCandidateView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        perfume = get_object_or_404(Perfume.objects.select_related("brand"), pk=request.POST.get("perfume_id"))
        variant = None
        if request.POST.get("variant_id"):
            variant = get_object_or_404(PerfumeVariant, pk=request.POST.get("variant_id"), perfume=perfume)

        with transaction.atomic():
            product = get_object_or_404(
                SupplierProduct.objects.select_for_update().select_related("supplier"),
                pk=supplier_product_id,
            )
            suggestion = (
                models.LinkSuggestion.objects.select_for_update()
                .filter(
                    supplier_product=product,
                    suggested_perfume=perfume,
                    suggested_variant=variant,
                )
                .order_by("-created_at", "-id")
                .first()
            )
            if suggestion and suggestion.status != models.LinkSuggestion.STATUS_PENDING:
                messages.warning(request, "This suggestion was already handled by another user.")
                return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)

            parsed = save_parse(product)
            brand_alias_text = (parsed.detected_brand_text or product.brand or perfume.brand.name).strip()
            product_alias_text = (parsed.product_name_text or perfume.name).strip()
            supplier = product.supplier if request.POST.get("alias_scope") == forms.ParseTeachingForm.SCOPE_SUPPLIER else None

            if brand_alias_text:
                models.BrandAlias.objects.update_or_create(
                    brand=perfume.brand,
                    supplier=supplier,
                    alias_text=brand_alias_text,
                    defaults={"normalized_alias": normalize_query(brand_alias_text), "priority": 10 if supplier else 50, "active": True},
                )
            if product_alias_text:
                models.ProductAlias.objects.update_or_create(
                    brand=perfume.brand,
                    perfume=perfume,
                    supplier=supplier,
                    alias_text=product_alias_text,
                    defaults={
                        "canonical_text": perfume.name,
                        "concentration": perfume.concentration,
                        "audience": perfume.audience,
                        "excluded_terms": request.POST.get("excluded_terms", ""),
                        "priority": 10 if supplier else 50,
                        "active": True,
                    },
                )

            parsed.normalized_brand = perfume.brand
            parsed.detected_brand_text = brand_alias_text or perfume.brand.name
            parsed.product_name_text = perfume.name
            parsed.concentration = perfume.concentration
            parsed.supplier_gender_hint = perfume.audience
            if variant:
                parsed.size_ml = variant.size_ml
                parsed.packaging = variant.packaging
                parsed.variant_type = variant.variant_type
                parsed.is_tester = variant.is_tester
            parsed.confidence = 100
            parsed.warnings = []
            parsed.locked_by_human = True
            parsed.last_parsed_at = timezone.now()
            parsed.save()

            had_link = bool(product.catalog_perfume_id or product.catalog_variant_id)
            product.catalog_perfume = perfume
            product.catalog_variant = variant
            product.save(update_fields=["catalog_perfume", "catalog_variant", "updated_at"])

            _record_manual_link_decision(
                supplier_product=product,
                perfume_id=perfume.id,
                variant_id=variant.id if variant else None,
                decision_type=models.ManualLinkDecision.DECISION_APPROVE_VARIANT if variant else models.ManualLinkDecision.DECISION_APPROVE_PERFUME,
                reason="Accepted from normalization catalogue candidates.",
                apply_to_similar=False,
                created_by=request.user,
                allow_overwrite=had_link,
            )
            if suggestion:
                suggestion.status = models.LinkSuggestion.STATUS_APPROVED
                suggestion.reviewed_by = request.user
                suggestion.reviewed_at = timezone.now()
                suggestion.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        messages.success(request, "Catalogue candidate accepted and parse locked.")
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class ReparseProductView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        product = get_object_or_404(SupplierProduct, pk=supplier_product_id)
        save_parse(product, force=request.POST.get("force") == "1")
        messages.success(request, "Product parsed.")
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class ExcludeGarbageKeywordView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        from assistant_core.models import GlobalRule
        from assistant_linking.services.garbage import clear_garbage_keyword_cache

        product = get_object_or_404(SupplierProduct.objects.select_related("supplier"), pk=supplier_product_id)
        keywords = normalize_garbage_keyword(request.POST.get("keywords", ""))
        if not keywords:
            messages.error(request, "Add at least one garbage keyword.")
            return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)

        for keyword in keywords.splitlines():
            GlobalRule.objects.update_or_create(
                rule_kind="garbage_keyword",
                scope_type="global",
                rule_text=keyword,
                defaults={
                    "title": f"Garbage keyword: {keyword}",
                    "scope_value": "",
                    "priority": 10,
                    "confidence": 100,
                    "active": True,
                    "approved": True,
                    "created_by": request.user,
                },
            )
        clear_garbage_keyword_cache()
        save_parse(product, force=True)
        messages.success(request, "Garbage keyword saved and this row was reparsed.")
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class LockParseView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        parsed = get_object_or_404(models.ParsedSupplierProduct, supplier_product_id=supplier_product_id)
        parsed.locked_by_human = True
        parsed.save(update_fields=["locked_by_human", "updated_at"])
        messages.success(request, "Parse locked.")
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class SaveBrandAliasView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        product = get_object_or_404(SupplierProduct.objects.select_related("supplier"), pk=supplier_product_id)
        form = forms.BrandAliasForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Brand alias saved.")
        else:
            messages.error(request, "Brand alias was not saved.")
            _mark_invalid_fields_for_a11y(form)
            return _render_normalization_detail(request, product, brand_alias_form=form)
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class SaveProductAliasView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        product = get_object_or_404(SupplierProduct.objects.select_related("supplier"), pk=supplier_product_id)
        form = forms.ProductAliasForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Product alias saved.")
        else:
            messages.error(request, "Product alias was not saved.")
            _mark_invalid_fields_for_a11y(form)
            return _render_normalization_detail(request, product, product_alias_form=form)
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class TeachParseView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        product = get_object_or_404(SupplierProduct.objects.select_related("supplier"), pk=supplier_product_id)
        parsed = save_parse(product)
        form = forms.ParseTeachingForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Teaching form has invalid values.")
            _mark_invalid_fields_for_a11y(form)
            return _render_normalization_detail(request, product, teach_form=form)

        data = form.cleaned_data
        brand_name = data["brand_name"].strip()
        product_name = data["product_name"].strip()
        brand = Brand.objects.filter(name__iexact=brand_name).first()
        if not brand:
            brand = Brand.objects.create(name=brand_name)
        supplier = product.supplier if data["alias_scope"] == forms.ParseTeachingForm.SCOPE_SUPPLIER else None

        brand_alias_text = (data.get("supplier_brand_text") or brand_name).strip()
        if brand_alias_text:
            models.BrandAlias.objects.get_or_create(
                brand=brand,
                supplier=supplier,
                alias_text=brand_alias_text,
                defaults={
                    "normalized_alias": normalize_query(brand_alias_text),
                    "priority": 10 if supplier else 50,
                    "active": True,
                },
            )

        product_alias_text = (data.get("supplier_product_text") or product_name).strip()
        if product_alias_text:
            models.ProductAlias.objects.update_or_create(
                brand=brand,
                supplier=supplier,
                alias_text=product_alias_text,
                defaults={
                    "canonical_text": product_name,
                    "concentration": data.get("concentration") or "",
                    "audience": data.get("audience") or "",
                    "excluded_terms": (data.get("product_excluded_terms") or "").strip(),
                    "priority": 10 if supplier else 50,
                    "active": True,
                },
            )

        parsed.normalized_brand = brand
        parsed.detected_brand_text = brand_alias_text
        parsed.product_name_text = product_name
        parsed.concentration = data.get("concentration") or ""
        parsed.size_ml = data.get("size_ml")
        parsed.raw_size_text = (data.get("supplier_size_text") or "").strip()
        parsed.supplier_gender_hint = data.get("audience") or ""
        parsed.packaging = (data.get("packaging") or "").strip().lower()
        parsed.variant_type = (data.get("variant_type") or "").strip().lower()
        parsed.is_sample = parsed.variant_type == "sample"
        parsed.is_travel = parsed.variant_type == "travel"
        parsed.is_set = parsed.variant_type == "set"
        parsed.is_tester = parsed.variant_type == "tester" or "tester" in parsed.packaging
        parsed.confidence = 100
        parsed.warnings = []
        parsed.locked_by_human = bool(data.get("lock_parse"))
        parsed.last_parsed_at = timezone.now()
        parsed.save()

        updated_similar = 0
        if data.get("reparse_similar"):
            selected_ids = {
                int(value)
                for value in request.POST.getlist("selected_similar_ids")
                if str(value).isdigit()
            }
            similar_terms = [brand_alias_text, product_alias_text, data.get("supplier_concentration_text"), data.get("supplier_size_text")]
            similar_filter = Q()
            for term in [term.strip() for term in similar_terms if term and term.strip()]:
                similar_filter |= Q(name__icontains=term)
            if similar_filter and selected_ids:
                similar = SupplierProduct.objects.filter(similar_filter, pk__in=selected_ids).exclude(pk=product.pk)[:500]
                for similar_product in similar:
                    save_parse(similar_product)
                    updated_similar += 1

        messages.success(
            request,
            f"Teaching saved. This product is now parsed as {brand.name} / {product_name}. Updated {updated_similar} selected preview rows.",
        )
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class GroupQueueView(StaffAssistantMixin, ListView):
    model = models.MatchGroup
    template_name = "assistant_linking/groups/queue.html"
    context_object_name = "groups"
    paginate_by = 50

    def get_queryset(self):
        queryset = models.MatchGroup.objects.select_related("normalized_brand", "candidate_perfume", "candidate_variant").annotate(item_count=Count("items"))
        status = self.request.GET.get("status")
        brand = self.request.GET.get("brand")
        if status:
            queryset = queryset.filter(status=status)
        if brand:
            queryset = queryset.filter(Q(normalized_brand__name__icontains=brand) | Q(canonical_name__icontains=brand))
        return queryset.order_by("status", "-confidence", "canonical_name")

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "last_link_action": _latest_undoable_action(self.request.user)}


class GroupDetailView(StaffAssistantMixin, DetailView):
    model = models.MatchGroup
    template_name = "assistant_linking/groups/detail.html"
    context_object_name = "group"
    pk_url_kwarg = "group_id"

    def get_context_data(self, **kwargs):
        items = self.object.items.select_related("supplier_product", "supplier_product__supplier", "parsed_product")
        return {**super().get_context_data(**kwargs), "items": items, "last_link_action": _latest_undoable_action(self.request.user)}


class RebuildGroupsView(StaffAssistantMixin, View):
    def post(self, request):
        count = rebuild_groups(only_open=request.POST.get("only_open") == "1")
        messages.success(request, f"Rebuilt {count} group memberships.")
        return redirect("assistant_linking:group_queue")


class GroupActionView(StaffAssistantMixin, View):
    def post(self, request, group_id, action):
        with transaction.atomic():
            group = get_object_or_404(models.MatchGroup.objects.select_for_update(), pk=group_id)
            item_ids = request.POST.getlist("item_ids")
            items = group.items.select_for_update().filter(id__in=item_ids)
            if action == "exclude":
                items.update(role=models.MatchGroupItem.ROLE_EXCLUDED, reasoning=request.POST.get("reason", ""))
            elif action == "split":
                for item in items:
                    new_group = models.MatchGroup.objects.create(
                        group_key=f"{group.group_key}|split|{item.supplier_product_id}|{timezone.now().timestamp()}",
                        normalized_brand=group.normalized_brand,
                        canonical_name=group.canonical_name,
                        concentration=group.concentration,
                        audience_hint=group.audience_hint,
                        size_ml=group.size_ml,
                        packaging=group.packaging,
                        variant_type=group.variant_type,
                        status=models.MatchGroup.STATUS_OPEN,
                        confidence=max(group.confidence - 10, 0),
                    )
                    item.match_group = new_group
                    item.role = models.MatchGroupItem.ROLE_SPLIT
                    item.reasoning = request.POST.get("reason", "Split by operator")
                    item.save()
        messages.success(request, "Group action applied.")
        return redirect("assistant_linking:group_detail", group_id=group_id)


class ProductWorkbenchView(StaffAssistantMixin, DetailView):
    model = SupplierProduct
    template_name = "assistant_linking/workbench/product.html"
    context_object_name = "product"
    pk_url_kwarg = "supplier_product_id"

    def get_context_data(self, **kwargs):
        product = self.object
        parsed = save_parse(product)
        group = models.MatchGroup.objects.filter(items__supplier_product=product).first()
        similar = SupplierProduct.objects.filter(assistant_group_items__match_group=group).select_related("supplier", "catalog_perfume", "catalog_variant") if group else SupplierProduct.objects.none()
        return {
            **super().get_context_data(**kwargs),
            "parsed": parsed,
            "group": group,
            "similar": similar,
            "suggestions": product.assistant_link_suggestions.select_related("suggested_perfume", "suggested_variant").order_by("-created_at")[:10],
            "same_supplier": SupplierProduct.objects.filter(supplier=product.supplier).exclude(pk=product.pk).order_by("-is_active", "name")[:25],
            "last_link_action": _latest_undoable_action(self.request.user),
        }


class GenerateSuggestionsView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        suggestions = generate_link_suggestions(supplier_product_id)
        messages.success(request, f"Generated {len(suggestions)} suggestions.")
        return redirect("assistant_linking:product_workbench", supplier_product_id=supplier_product_id)


class BulkLinkView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        source = get_object_or_404(
            SupplierProduct.objects.select_related("catalog_perfume", "catalog_variant"),
            pk=supplier_product_id,
        )
        perfume_id = request.POST.get("perfume_id") or source.catalog_perfume_id
        variant_id = request.POST.get("variant_id") or source.catalog_variant_id
        if not perfume_id:
            messages.error(request, "Choose or approve a catalogue perfume before linking rows.")
            return redirect("assistant_linking:product_workbench", supplier_product_id=supplier_product_id)
        allow_overwrite = request.POST.get("confirm_overwrite") == "1"
        apply_to_similar = request.POST.get("apply_to_similar") == "1"
        if apply_to_similar:
            group = models.MatchGroup.objects.filter(items__supplier_product=source).first()
            queryset = SupplierProduct.objects.filter(assistant_group_items__match_group=group) if group else SupplierProduct.objects.filter(pk=source.pk)
            product_ids = list(queryset.order_by("id").values_list("id", flat=True))
            if request.POST.get("confirm_apply_to_similar") != "1":
                return HttpResponse(
                    f"Confirm apply_to_similar before linking {len(product_ids)} matched products.",
                    status=409,
                )
        else:
            product_ids = [int(value) for value in request.POST.getlist("supplier_product_ids") if str(value).isdigit()] or [source.id]

        product_ids = list(dict.fromkeys(product_ids))
        if len(product_ids) > BULK_LINK_PRODUCT_CAP:
            return HttpResponse(
                f"Bulk link matched {len(product_ids)} products; narrow scope to {BULK_LINK_PRODUCT_CAP} or fewer.",
                status=409,
            )

        action = _bulk_link_products(
            user=request.user,
            product_ids=product_ids,
            perfume_id=int(perfume_id) if perfume_id else None,
            variant_id=int(variant_id) if variant_id else None,
            allow_overwrite=allow_overwrite,
            apply_to_similar=apply_to_similar,
            reason=request.POST.get("reason", ""),
        )
        linked = action.payload_json.get("linked", 0)
        if len(product_ids) > 20 or request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "job_id": action.id,
                    "status_url": reverse_lazy("assistant_linking:bulk_link_status", kwargs={"action_id": action.id}),
                    "undo_url": reverse_lazy("assistant_linking:undo_link_action", kwargs={"action_id": action.id}),
                },
                status=202,
            )
        messages.success(request, f"Linked {linked} products.")
        return redirect("assistant_linking:product_workbench", supplier_product_id=supplier_product_id)


class BulkLinkStatusView(StaffAssistantMixin, View):
    def get(self, request, action_id):
        action = get_object_or_404(models.LinkAction, pk=action_id, user=request.user)
        payload = action.payload_json or {}
        matched = int(payload.get("matched") or 0)
        linked = int(payload.get("linked") or 0)
        skipped = int(payload.get("skipped") or 0)
        processed = linked + skipped
        return JsonResponse(
            {
                "job_id": action.id,
                "status": payload.get("status", "COMPLETE"),
                "matched": matched,
                "linked": linked,
                "skipped": skipped,
                "processed": processed,
                "percent": 100 if matched else 0,
                "undo_url": reverse_lazy("assistant_linking:undo_link_action", kwargs={"action_id": action.id}),
            }
        )


class UndoLinkActionView(StaffAssistantMixin, View):
    def post(self, request, action_id):
        cutoff = timezone.now() - timedelta(seconds=UNDO_WINDOW_SECONDS)
        action = get_object_or_404(
            models.LinkAction,
            pk=action_id,
            user=request.user,
            action_type=models.LinkAction.ACTION_BULK_LINK,
            created_at__gte=cutoff,
        )
        if (action.payload_json or {}).get("status") == "UNDONE":
            raise Http404("Action already undone.")
        restored = _undo_link_action(action, request.user)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"restored": restored, "status": "UNDONE"})
        messages.success(request, f"Undid {restored} linked product(s).")
        return redirect(request.POST.get("next") or "assistant_linking:group_queue")
