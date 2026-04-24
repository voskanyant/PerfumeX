from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView, View

from assistant_linking import forms, models
from assistant_linking.services.catalog_matcher import candidate_matches, rule_impact, similar_supplier_rows
from assistant_linking.services.grouping import rebuild_groups
from assistant_linking.services.mock_suggester import generate_link_suggestions
from assistant_linking.services.normalizer import PARSER_VERSION, save_parse
from assistant_linking.services.smart_search import normalize_query
from catalog.models import Brand, Perfume, PerfumeVariant
from prices.models import SupplierProduct
from prices.services.product_visibility import (
    apply_hidden_product_keywords,
    get_hidden_product_keywords_for_user,
)


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


class StaffAssistantMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return bool(self.request.user and self.request.user.is_staff)


class NormalizationDashboardView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_linking/normalization/dashboard.html"

    def get_context_data(self, **kwargs):
        hidden_keywords = _hidden_product_keywords(self.request)
        parsed_queryset = _hide_parsed_products(
            models.ParsedSupplierProduct.objects.all(),
            self.request,
        )
        unparsed_queryset = _hide_supplier_products(
            SupplierProduct.objects.all(),
            self.request,
        )
        return {
            **super().get_context_data(**kwargs),
            "parsed_count": parsed_queryset.count(),
            "unparsed_count": unparsed_queryset.filter(assistant_parse__isnull=True).count(),
            "low_confidence_count": parsed_queryset.filter(confidence__lt=75).count(),
            "missing_brand_count": parsed_queryset.filter(normalized_brand__isnull=True).count(),
            "missing_size_count": parsed_queryset.filter(size_ml__isnull=True).count(),
            "modifier_count": parsed_queryset.exclude(modifiers=[]).count(),
            "tester_sample_count": parsed_queryset.filter(
                Q(is_tester=True) | Q(is_sample=True) | Q(is_travel=True) | Q(is_set=True)
            ).count(),
            "recent": parsed_queryset.select_related("supplier_product", "normalized_brand").order_by("-updated_at")[:20],
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
        query = self.get_search_query()
        if query:
            queryset = queryset.filter(
                Q(supplier_product__supplier__name__icontains=query)
                | Q(supplier_product__name__icontains=query)
                | Q(supplier_product__supplier_sku__icontains=query)
                | Q(supplier_product__brand__icontains=query)
                | Q(normalized_brand__name__icontains=query)
                | Q(detected_brand_text__icontains=query)
                | Q(product_name_text__icontains=query)
                | Q(concentration__icontains=query)
            )
        queryset = _hide_parsed_products(queryset, self.request)
        return queryset.order_by("confidence", "supplier_product__supplier__name", "supplier_product__name")

    def parse_matches_view(self, parsed):
        return parsed.confidence < 75

    def should_refresh_parse(self, parsed) -> bool:
        if parsed.locked_by_human:
            return False
        if parsed.parser_version != PARSER_VERSION:
            return True
        if not parsed.last_parsed_at:
            return True
        product_updated_at = getattr(parsed.supplier_product, "updated_at", None)
        if product_updated_at and product_updated_at > parsed.last_parsed_at:
            return True
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_obj = context.get("page_obj")
        if not page_obj:
            return context

        refreshed = []
        for stored_parse in list(page_obj.object_list):
            refreshed_parse = (
                save_parse(stored_parse.supplier_product)
                if self.should_refresh_parse(stored_parse)
                else stored_parse
            )
            if self.parse_matches_view(refreshed_parse):
                refreshed.append(refreshed_parse)

        page_obj.object_list = refreshed
        context["object_list"] = refreshed
        context[self.context_object_name] = refreshed
        return context


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
        queryset = _hide_parsed_products(queryset, self.request)
        return queryset.order_by("supplier_product__name")

    def parse_matches_view(self, parsed):
        return parsed.normalized_brand_id is None


class MissingSizeListView(NormalizationIssueListView):
    issue_title = "Missing or ambiguous size"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(size_ml__isnull=True)
        queryset = _hide_parsed_products(queryset, self.request)
        return queryset.order_by("supplier_product__name")

    def parse_matches_view(self, parsed):
        return parsed.size_ml is None


class TesterSampleListView(NormalizationIssueListView):
    issue_title = "Tester, sample, travel, and set rows"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).filter(Q(is_tester=True) | Q(is_sample=True) | Q(is_travel=True) | Q(is_set=True))
        queryset = _hide_parsed_products(queryset, self.request)
        return queryset.order_by("supplier_product__name")

    def parse_matches_view(self, parsed):
        return bool(parsed.is_tester or parsed.is_sample or parsed.is_travel or parsed.is_set)


class ModifierConflictListView(NormalizationIssueListView):
    issue_title = "Identity modifiers"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        ).exclude(modifiers=[])
        queryset = _hide_parsed_products(queryset, self.request)
        return queryset.order_by("supplier_product__name")

    def parse_matches_view(self, parsed):
        return bool(parsed.modifiers)


class ParsedListView(NormalizationIssueListView):
    issue_title = "Parsed products"

    def get_queryset(self):
        queryset = models.ParsedSupplierProduct.objects.select_related(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        )
        query = self.get_search_query()
        if query:
            queryset = queryset.filter(
                Q(supplier_product__supplier__name__icontains=query)
                | Q(supplier_product__name__icontains=query)
                | Q(supplier_product__supplier_sku__icontains=query)
                | Q(supplier_product__brand__icontains=query)
                | Q(normalized_brand__name__icontains=query)
                | Q(detected_brand_text__icontains=query)
                | Q(product_name_text__icontains=query)
                | Q(concentration__icontains=query)
            )
        queryset = _hide_parsed_products(queryset, self.request)
        return queryset.order_by("-updated_at", "supplier_product__supplier__name", "supplier_product__name")

    def parse_matches_view(self, parsed):
        return True


class ParsedProductDetailView(StaffAssistantMixin, DetailView):
    model = SupplierProduct
    template_name = "assistant_linking/normalization/detail.html"
    context_object_name = "product"
    pk_url_kwarg = "supplier_product_id"

    def get_context_data(self, **kwargs):
        product = self.object
        parsed = save_parse(product)
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
        teach_initial = {
            "supplier_brand_text": brand_alias_text,
            "brand_name": parsed.normalized_brand.name if parsed.normalized_brand_id else parsed.detected_brand_text,
            "supplier_product_text": product_alias_text,
            "product_name": parsed.product_name_text,
            "product_excluded_terms": existing_blockers,
            "supplier_concentration_text": parsed.concentration,
            "concentration": parsed.concentration,
            "supplier_size_text": parsed.raw_size_text or product.size,
            "size_ml": parsed.size_ml,
            "supplier_audience_text": parsed.supplier_gender_hint,
            "audience": parsed.supplier_gender_hint,
            "supplier_type_text": parsed.variant_type,
            "variant_type": parsed.variant_type,
            "supplier_packaging_text": parsed.packaging,
            "packaging": parsed.packaging,
            "alias_scope": forms.ParseTeachingForm.SCOPE_GLOBAL,
            "lock_parse": True,
            "reparse_similar": False,
        }
        return {
            **super().get_context_data(**kwargs),
            "parsed": parsed,
            "teach_form": forms.ParseTeachingForm(initial=teach_initial),
            "catalog_candidates": candidate_matches(parsed),
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


class AcceptCatalogCandidateView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        product = get_object_or_404(SupplierProduct.objects.select_related("supplier"), pk=supplier_product_id)
        parsed = save_parse(product)
        perfume = get_object_or_404(Perfume.objects.select_related("brand"), pk=request.POST.get("perfume_id"))
        variant = None
        if request.POST.get("variant_id"):
            variant = get_object_or_404(PerfumeVariant, pk=request.POST.get("variant_id"), perfume=perfume)

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

        product.catalog_perfume = perfume
        if variant:
            product.catalog_variant = variant
        product.save(update_fields=["catalog_perfume", "catalog_variant", "updated_at"])

        models.ManualLinkDecision.objects.create(
            supplier_product=product,
            perfume=perfume,
            variant=variant,
            decision_type=models.ManualLinkDecision.DECISION_APPROVE_VARIANT if variant else models.ManualLinkDecision.DECISION_APPROVE_PERFUME,
            reason="Accepted from normalization catalogue candidates.",
            apply_to_similar=False,
            created_by=request.user,
        )
        messages.success(request, "Catalogue candidate accepted and parse locked.")
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class ReparseProductView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        product = get_object_or_404(SupplierProduct, pk=supplier_product_id)
        save_parse(product, force=request.POST.get("force") == "1")
        messages.success(request, "Product parsed.")
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
        form = forms.BrandAliasForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Brand alias saved.")
        else:
            messages.error(request, "Brand alias was not saved.")
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class SaveProductAliasView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        form = forms.ProductAliasForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Product alias saved.")
        else:
            messages.error(request, "Product alias was not saved.")
        return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)


class TeachParseView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        product = get_object_or_404(SupplierProduct.objects.select_related("supplier"), pk=supplier_product_id)
        parsed = save_parse(product)
        form = forms.ParseTeachingForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Teaching form has invalid values.")
            return redirect("assistant_linking:normalization_detail", supplier_product_id=supplier_product_id)

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


class GroupDetailView(StaffAssistantMixin, DetailView):
    model = models.MatchGroup
    template_name = "assistant_linking/groups/detail.html"
    context_object_name = "group"
    pk_url_kwarg = "group_id"

    def get_context_data(self, **kwargs):
        items = self.object.items.select_related("supplier_product", "supplier_product__supplier", "parsed_product")
        return {**super().get_context_data(**kwargs), "items": items}


class RebuildGroupsView(StaffAssistantMixin, View):
    def post(self, request):
        count = rebuild_groups(only_open=request.POST.get("only_open") == "1")
        messages.success(request, f"Rebuilt {count} group memberships.")
        return redirect("assistant_linking:group_queue")


class GroupActionView(StaffAssistantMixin, View):
    def post(self, request, group_id, action):
        group = get_object_or_404(models.MatchGroup, pk=group_id)
        item_ids = request.POST.getlist("item_ids")
        items = group.items.filter(id__in=item_ids)
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
        }


class GenerateSuggestionsView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        suggestions = generate_link_suggestions(supplier_product_id)
        messages.success(request, f"Generated {len(suggestions)} suggestions.")
        return redirect("assistant_linking:product_workbench", supplier_product_id=supplier_product_id)


class BulkLinkView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        source = get_object_or_404(SupplierProduct, pk=supplier_product_id)
        perfume_id = request.POST.get("perfume_id") or source.catalog_perfume_id
        variant_id = request.POST.get("variant_id") or source.catalog_variant_id
        if not perfume_id:
            messages.error(request, "Choose or approve a catalogue perfume before linking rows.")
            return redirect("assistant_linking:product_workbench", supplier_product_id=supplier_product_id)
        allow_overwrite = request.POST.get("confirm_overwrite") == "1"
        product_ids = request.POST.getlist("supplier_product_ids") or [str(source.id)]
        linked = 0
        for product in SupplierProduct.objects.filter(id__in=product_ids):
            if (product.catalog_perfume_id or product.catalog_variant_id) and not allow_overwrite:
                continue
            if perfume_id:
                product.catalog_perfume_id = perfume_id
            if variant_id:
                product.catalog_variant_id = variant_id
            product.save(update_fields=["catalog_perfume", "catalog_variant", "updated_at"])
            models.ManualLinkDecision.objects.create(
                supplier_product=product,
                perfume_id=perfume_id or None,
                variant_id=variant_id or None,
                decision_type=models.ManualLinkDecision.DECISION_APPROVE_VARIANT if variant_id else models.ManualLinkDecision.DECISION_APPROVE_PERFUME,
                reason=request.POST.get("reason", ""),
                apply_to_similar=bool(product_ids),
                created_by=request.user,
            )
            linked += 1
        messages.success(request, f"Linked {linked} products.")
        return redirect("assistant_linking:product_workbench", supplier_product_id=supplier_product_id)
