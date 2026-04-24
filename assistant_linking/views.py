from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView, View

from assistant_linking import forms, models
from assistant_linking.services.grouping import rebuild_groups
from assistant_linking.services.mock_suggester import generate_link_suggestions
from assistant_linking.services.normalizer import save_parse
from prices.models import SupplierProduct


class StaffAssistantMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return bool(self.request.user and self.request.user.is_staff)


class NormalizationDashboardView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_linking/normalization/dashboard.html"

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "parsed_count": models.ParsedSupplierProduct.objects.count(),
            "unparsed_count": SupplierProduct.objects.filter(assistant_parse__isnull=True).count(),
            "low_confidence_count": models.ParsedSupplierProduct.objects.filter(confidence__lt=75).count(),
            "recent": models.ParsedSupplierProduct.objects.select_related("supplier_product", "normalized_brand").order_by("-updated_at")[:20],
        }


class UnparsedListView(StaffAssistantMixin, ListView):
    model = SupplierProduct
    template_name = "assistant_linking/normalization/product_list.html"
    context_object_name = "products"
    paginate_by = 100

    def get_queryset(self):
        return SupplierProduct.objects.select_related("supplier").filter(assistant_parse__isnull=True).order_by("supplier__name", "name")


class LowConfidenceListView(StaffAssistantMixin, ListView):
    model = models.ParsedSupplierProduct
    template_name = "assistant_linking/normalization/low_confidence.html"
    context_object_name = "parses"
    paginate_by = 100

    def get_queryset(self):
        return models.ParsedSupplierProduct.objects.select_related("supplier_product", "supplier_product__supplier", "normalized_brand").filter(confidence__lt=75).order_by("confidence")


class ParsedProductDetailView(StaffAssistantMixin, DetailView):
    model = SupplierProduct
    template_name = "assistant_linking/normalization/detail.html"
    context_object_name = "product"
    pk_url_kwarg = "supplier_product_id"

    def get_context_data(self, **kwargs):
        product = self.object
        return {**super().get_context_data(**kwargs), "parsed": save_parse(product)}


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
