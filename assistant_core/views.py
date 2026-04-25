from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
import re
from collections import defaultdict

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.core.paginator import Paginator
from django.views.generic import CreateView, DeleteView, DetailView, ListView, RedirectView, TemplateView, UpdateView, View

from assistant_core import forms, models
from assistant_core.services.catalog_importer import import_catalog_file
from assistant_core.services.mock_brand_research import run_mock_brand_watch
from assistant_core.services.mock_description_generator import create_mock_draft
from assistant_linking import forms as linking_forms
from catalog.models import AIDraft, Brand, FactClaim, Perfume, PerfumeVariant, Source


class StaffAssistantMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return bool(self.request.user and self.request.user.is_staff)


class DashboardView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/dashboard.html"

    def get_context_data(self, **kwargs):
        from assistant_linking.models import BrandAlias, ConcentrationAlias, LinkSuggestion, ManualLinkDecision, ParsedSupplierProduct, ProductAlias

        knowledge_count = (
            models.GlobalRule.objects.count()
            + models.SupplierRule.objects.count()
            + models.KnowledgeNote.objects.count()
            + BrandAlias.objects.count()
            + ProductAlias.objects.count()
            + ConcentrationAlias.objects.count()
        )

        return {
            **super().get_context_data(**kwargs),
            "cards": [
                ("Normalisation", "assistant_linking:normalization_dashboard", ParsedSupplierProduct.objects.filter(confidence__lt=75).count()),
                ("Catalogue", "prices:our_product_list", Perfume.objects.count()),
                ("Linking Workbench", "assistant_linking:group_queue", LinkSuggestion.objects.filter(status=LinkSuggestion.STATUS_PENDING).count()),
                ("Knowledge Base", "assistant_core:knowledge", knowledge_count),
                ("Brand Managers", "assistant_core:brand_manager_list", models.BrandWatchProfile.objects.filter(active=True).count()),
                ("Research Review", "assistant_core:research_jobs", models.DetectedChange.objects.filter(status=models.DetectedChange.STATUS_PENDING).count()),
                ("AI Drafts", "assistant_core:drafts", AIDraft.objects.filter(status=AIDraft.STATUS_PENDING).count()),
            ],
            "pending_approvals": models.DetectedChange.objects.filter(status=models.DetectedChange.STATUS_PENDING).count() + FactClaim.objects.filter(status=FactClaim.STATUS_PENDING).count(),
            "low_confidence": ParsedSupplierProduct.objects.filter(confidence__lt=75).count(),
            "recent_decisions": ManualLinkDecision.objects.select_related("supplier_product").order_by("-created_at")[:8],
        }


class KnowledgeView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/knowledge/index.html"

    def get_context_data(self, **kwargs):
        from assistant_linking.models import BrandAlias, ConcentrationAlias, ManualLinkDecision, ProductAlias

        return {
            **super().get_context_data(**kwargs),
            "global_rules": models.GlobalRule.objects.order_by("priority", "title"),
            "supplier_rules": models.SupplierRule.objects.select_related("supplier", "brand").order_by("supplier__name", "priority"),
            "notes": models.KnowledgeNote.objects.select_related("supplier", "brand", "perfume").order_by("category", "title"),
            "brand_aliases": BrandAlias.objects.select_related("brand", "supplier").order_by("supplier__name", "priority", "alias_text"),
            "product_aliases": ProductAlias.objects.select_related("brand", "perfume", "supplier").order_by("supplier__name", "priority", "alias_text"),
            "concentration_alias_count": ConcentrationAlias.objects.count(),
            "manual_decisions": ManualLinkDecision.objects.select_related(
                "supplier_product",
                "supplier_product__supplier",
                "perfume",
                "variant",
            ).order_by("-created_at")[:50],
        }


class RulesView(KnowledgeView):
    pass


class AliasesView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/knowledge/aliases.html"
    paginate_by = 50

    SECTION_BRANDS = "brands"
    SECTION_PRODUCTS = "products"
    SECTION_CONCENTRATIONS = "concentrations"
    SECTION_CHOICES = {SECTION_BRANDS, SECTION_PRODUCTS, SECTION_CONCENTRATIONS}

    def _active_section(self):
        section = self.request.GET.get("section", self.SECTION_BRANDS).strip()
        return section if section in self.SECTION_CHOICES else self.SECTION_BRANDS

    def _filter_scope(self, queryset):
        scope = self.request.GET.get("scope", "all").strip()
        if scope == "global":
            queryset = queryset.filter(supplier__isnull=True)
        elif scope == "supplier":
            queryset = queryset.filter(supplier__isnull=False)
        return queryset, scope

    def _filter_status(self, queryset):
        status = self.request.GET.get("status", "active").strip()
        if status == "active":
            queryset = queryset.filter(active=True)
        elif status == "inactive":
            queryset = queryset.filter(active=False)
        return queryset, status

    def _brand_queryset(self, query):
        from assistant_linking.models import BrandAlias

        queryset = BrandAlias.objects.select_related("brand", "supplier").order_by("supplier__name", "priority", "alias_text")
        if query:
            queryset = queryset.filter(
                Q(alias_text__icontains=query)
                | Q(normalized_alias__icontains=query)
                | Q(brand__name__icontains=query)
                | Q(supplier__name__icontains=query)
            )
        return queryset

    def _product_queryset(self, query):
        from assistant_linking.models import ProductAlias

        queryset = ProductAlias.objects.select_related("brand", "perfume", "supplier").order_by("supplier__name", "priority", "alias_text")
        if query:
            queryset = queryset.filter(
                Q(alias_text__icontains=query)
                | Q(canonical_text__icontains=query)
                | Q(excluded_terms__icontains=query)
                | Q(concentration__icontains=query)
                | Q(audience__icontains=query)
                | Q(brand__name__icontains=query)
                | Q(perfume__name__icontains=query)
                | Q(supplier__name__icontains=query)
            )
        return queryset

    def _concentration_queryset(self, query):
        from assistant_linking.models import ConcentrationAlias

        queryset = ConcentrationAlias.objects.select_related("supplier").order_by("supplier__name", "priority", "alias_text")
        if query:
            queryset = queryset.filter(
                Q(alias_text__icontains=query)
                | Q(normalized_alias__icontains=query)
                | Q(concentration__icontains=query)
                | Q(supplier__name__icontains=query)
            )
        return queryset

    def get_context_data(self, **kwargs):
        from assistant_linking.models import BrandAlias, ConcentrationAlias, ProductAlias

        context = super().get_context_data(**kwargs)
        section = self._active_section()
        query = self.request.GET.get("q", "").strip()

        if section == self.SECTION_PRODUCTS:
            queryset = self._product_queryset(query)
        elif section == self.SECTION_CONCENTRATIONS:
            queryset = self._concentration_queryset(query)
        else:
            queryset = self._brand_queryset(query)

        queryset, scope = self._filter_scope(queryset)
        queryset, status = self._filter_status(queryset)
        page_obj = Paginator(queryset, self.paginate_by).get_page(self.request.GET.get("page") or 1)

        sections = [
            {
                "key": self.SECTION_BRANDS,
                "label": "Brand aliases",
                "count": BrandAlias.objects.count(),
                "create_url": reverse_lazy("assistant_core:brand_alias_create"),
            },
            {
                "key": self.SECTION_PRODUCTS,
                "label": "Product aliases",
                "count": ProductAlias.objects.count(),
                "create_url": reverse_lazy("assistant_core:product_alias_create"),
            },
            {
                "key": self.SECTION_CONCENTRATIONS,
                "label": "Concentration aliases",
                "count": ConcentrationAlias.objects.count(),
                "create_url": reverse_lazy("assistant_core:concentration_alias_create"),
            },
        ]

        context.update(
            {
                "active_section": section,
                "sections": sections,
                "query": query,
                "scope": scope,
                "status": status,
                "page_obj": page_obj,
                "items": page_obj.object_list,
                "create_url": next(item["create_url"] for item in sections if item["key"] == section),
            }
        )
        return context


class AliasManageMixin(StaffAssistantMixin):
    template_name = "assistant_core/knowledge/alias_form.html"
    success_section = "brands"

    def get_success_url(self):
        return f"{reverse_lazy('assistant_core:aliases')}?section={self.success_section}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_url"] = self.get_success_url()
        context["active_section"] = self.success_section
        return context


class BrandAliasCreateView(AliasManageMixin, CreateView):
    from assistant_linking.models import BrandAlias as _BrandAlias

    model = _BrandAlias
    form_class = linking_forms.BrandAliasForm
    template_name = "assistant_core/knowledge/alias_form.html"
    success_section = "brands"


class BrandAliasUpdateView(AliasManageMixin, UpdateView):
    from assistant_linking.models import BrandAlias as _BrandAlias

    model = _BrandAlias
    form_class = linking_forms.BrandAliasForm
    template_name = "assistant_core/knowledge/alias_form.html"
    success_section = "brands"


class BrandAliasDeleteView(StaffAssistantMixin, DeleteView):
    from assistant_linking.models import BrandAlias as _BrandAlias

    model = _BrandAlias
    template_name = "assistant_core/knowledge/alias_confirm_delete.html"

    def get_success_url(self):
        return f"{reverse_lazy('assistant_core:aliases')}?section=brands"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_url"] = self.get_success_url()
        return context


class ProductAliasCreateView(AliasManageMixin, CreateView):
    from assistant_linking.models import ProductAlias as _ProductAlias

    model = _ProductAlias
    form_class = linking_forms.ProductAliasForm
    success_section = "products"


class ProductAliasUpdateView(AliasManageMixin, UpdateView):
    from assistant_linking.models import ProductAlias as _ProductAlias

    model = _ProductAlias
    form_class = linking_forms.ProductAliasForm
    success_section = "products"


class ProductAliasDeleteView(StaffAssistantMixin, DeleteView):
    from assistant_linking.models import ProductAlias as _ProductAlias

    model = _ProductAlias
    template_name = "assistant_core/knowledge/alias_confirm_delete.html"

    def get_success_url(self):
        return f"{reverse_lazy('assistant_core:aliases')}?section=products"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_url"] = self.get_success_url()
        return context


class ConcentrationAliasCreateView(AliasManageMixin, CreateView):
    from assistant_linking.models import ConcentrationAlias as _ConcentrationAlias

    model = _ConcentrationAlias
    form_class = linking_forms.ConcentrationAliasForm
    success_section = "concentrations"


class ConcentrationAliasUpdateView(AliasManageMixin, UpdateView):
    from assistant_linking.models import ConcentrationAlias as _ConcentrationAlias

    model = _ConcentrationAlias
    form_class = linking_forms.ConcentrationAliasForm
    success_section = "concentrations"


class ConcentrationAliasDeleteView(StaffAssistantMixin, DeleteView):
    from assistant_linking.models import ConcentrationAlias as _ConcentrationAlias

    model = _ConcentrationAlias
    template_name = "assistant_core/knowledge/alias_confirm_delete.html"

    def get_success_url(self):
        return f"{reverse_lazy('assistant_core:aliases')}?section=concentrations"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["return_url"] = self.get_success_url()
        return context

    def get_context_data(self, **kwargs):
        from assistant_linking.models import BrandAlias, ProductAlias

        return {
            **super().get_context_data(**kwargs),
            "brand_aliases": BrandAlias.objects.select_related("brand", "supplier").order_by("supplier__name", "priority", "alias_text"),
            "product_aliases": ProductAlias.objects.select_related("brand", "perfume", "supplier").order_by("supplier__name", "priority", "alias_text"),
        }


class GlobalRuleCreateView(StaffAssistantMixin, CreateView):
    model = models.GlobalRule
    form_class = forms.GlobalRuleForm
    template_name = "assistant_core/form.html"
    success_url = reverse_lazy("assistant_core:knowledge")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class SupplierRuleCreateView(GlobalRuleCreateView):
    model = models.SupplierRule
    form_class = forms.SupplierRuleForm


class KnowledgeNoteCreateView(GlobalRuleCreateView):
    model = models.KnowledgeNote
    form_class = forms.KnowledgeNoteForm


class RuleDisableView(StaffAssistantMixin, View):
    def post(self, request, model_name, pk):
        model = models.GlobalRule if model_name == "global" else models.SupplierRule
        rule = get_object_or_404(model, pk=pk)
        rule.active = False
        rule.save(update_fields=["active", "updated_at"])
        messages.success(request, "Rule disabled.")
        return redirect("assistant_core:knowledge")


class TeachFromDecisionView(StaffAssistantMixin, View):
    def post(self, request):
        from assistant_linking.models import ManualLinkDecision

        decision = get_object_or_404(ManualLinkDecision, pk=request.POST.get("decision_id"))
        scope = request.POST.get("scope", "supplier")
        title = f"Decision rule from {decision.supplier_product_id}"
        if scope == "global":
            models.GlobalRule.objects.create(
                title=title,
                rule_kind="linking",
                scope_type="global",
                rule_text=decision.reason or decision.decision_type,
                approved=False,
                created_by=request.user,
            )
        else:
            models.SupplierRule.objects.create(
                supplier=decision.supplier_product.supplier,
                title=title,
                rule_kind="linking",
                rule_text=decision.reason or decision.decision_type,
                approved=False,
                created_by=request.user,
            )
        messages.success(request, "Teaching rule draft created.")
        return redirect("assistant_core:knowledge")


class BrandWatchProfileListView(StaffAssistantMixin, ListView):
    model = models.BrandWatchProfile
    template_name = "assistant_core/brand_managers/list.html"
    context_object_name = "profiles"


class CatalogContextMixin:
    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "concentrations": Perfume.objects.exclude(concentration="").values_list("concentration", flat=True).distinct().order_by("concentration"),
            "audiences": Perfume.objects.exclude(audience="").values_list("audience", flat=True).distinct().order_by("audience"),
            "packagings": PerfumeVariant.objects.exclude(packaging="").values_list("packaging", flat=True).distinct().order_by("packaging"),
            "variant_types": PerfumeVariant.objects.exclude(variant_type="").values_list("variant_type", flat=True).distinct().order_by("variant_type"),
        }


class CatalogBrandListView(StaffAssistantMixin, ListView):
    model = Brand
    template_name = "assistant_core/catalog/brands.html"
    context_object_name = "brands"
    paginate_by = 50

    def get_queryset(self):
        queryset = Brand.objects.annotate(perfume_count=Count("perfumes"))
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(Q(name__icontains=query) | Q(country_of_origin__icontains=query))
        return queryset.order_by("name")


class CatalogPerfumeListView(StaffAssistantMixin, RedirectView):
    pattern_name = "prices:our_product_list"
    query_string = True


class CatalogVariantListView(StaffAssistantMixin, ListView):
    model = PerfumeVariant
    template_name = "assistant_core/catalog/variants.html"
    context_object_name = "variants"
    paginate_by = 50

    def get_queryset(self):
        queryset = PerfumeVariant.objects.select_related("perfume", "perfume__brand")
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(perfume__name__icontains=query)
                | Q(perfume__brand__name__icontains=query)
                | Q(packaging__icontains=query)
                | Q(variant_type__icontains=query)
                | Q(ean__icontains=query)
                | Q(sku__icontains=query)
            )
        return queryset.order_by("perfume__brand__name", "perfume__name", "size_ml")


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
        return {**super().get_context_data(**kwargs), "form": kwargs.get("form") or forms.CatalogImportForm()}

    def post(self, request):
        form = forms.CatalogImportForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Catalogue file was not imported.")
            return self.render_to_response(self.get_context_data(form=form))
        try:
            result = import_catalog_file(
                form.cleaned_data["file"],
                create_aliases=form.cleaned_data["create_aliases"],
                update_existing=form.cleaned_data["update_existing"],
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return self.render_to_response(self.get_context_data(form=form))
        messages.success(request, f"Imported {result.rows_imported} catalogue rows.")
        return self.render_to_response(self.get_context_data(form=forms.CatalogImportForm(), result=result))


def _catalog_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


class CatalogCleanupView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/catalog/cleanup.html"

    def get_context_data(self, **kwargs):
        brand_groups = defaultdict(list)
        for brand in Brand.objects.order_by("name"):
            brand_groups[_catalog_key(brand.name)].append(brand)
        perfume_groups = defaultdict(list)
        for perfume in Perfume.objects.select_related("brand").order_by("brand__name", "name"):
            perfume_groups[(perfume.brand_id, _catalog_key(perfume.name), perfume.concentration or "")].append(perfume)
        return {
            **super().get_context_data(**kwargs),
            "brand_duplicates": [items for items in brand_groups.values() if len(items) > 1],
            "perfume_duplicates": [items for items in perfume_groups.values() if len(items) > 1],
            "brand_merge_form": forms.CatalogBrandMergeForm(),
            "perfume_merge_form": forms.CatalogPerfumeMergeForm(),
        }


class CatalogBrandMergeView(StaffAssistantMixin, View):
    def post(self, request):
        form = forms.CatalogBrandMergeForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Brand merge was not saved.")
            return redirect("assistant_core:catalog_cleanup")
        source = form.cleaned_data["source"]
        target = form.cleaned_data["target"]
        with transaction.atomic():
            source.perfumes.update(brand=target)
            source.aliases.update(brand=target)
            source.product_aliases.update(brand=target)
            models.KnowledgeNote.objects.filter(brand=source).update(brand=target)
            models.SupplierRule.objects.filter(brand=source).update(brand=target)
            source.delete()
        messages.success(request, f"Merged brand into {target.name}.")
        return redirect("assistant_core:catalog_cleanup")


class CatalogPerfumeMergeView(StaffAssistantMixin, View):
    def post(self, request):
        form = forms.CatalogPerfumeMergeForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Perfume merge was not saved.")
            return redirect("assistant_core:catalog_cleanup")
        source = form.cleaned_data["source"]
        target = form.cleaned_data["target"]
        with transaction.atomic():
            for variant in source.variants.all():
                duplicate = target.variants.filter(
                    size_ml=variant.size_ml,
                    packaging=variant.packaging,
                    variant_type=variant.variant_type,
                    is_tester=variant.is_tester,
                ).first()
                if duplicate:
                    variant.delete()
                else:
                    variant.perfume = target
                    variant.save(update_fields=["perfume"])
            source.sources.update(perfume=target)
            source.fact_claims.update(perfume=target)
            source.ai_drafts.update(perfume=target)
            source.perfume_notes.update(perfume=target)
            source.perfume_accords.update(perfume=target)
            source.product_aliases.update(perfume=target, brand=target.brand)
            models.KnowledgeNote.objects.filter(perfume=source).update(perfume=target)
            from assistant_linking.models import LinkSuggestion, ManualLinkDecision
            from prices.models import SupplierProduct

            SupplierProduct.objects.filter(catalog_perfume=source).update(catalog_perfume=target)
            ManualLinkDecision.objects.filter(perfume=source).update(perfume=target)
            LinkSuggestion.objects.filter(suggested_perfume=source).update(suggested_perfume=target)
            source.delete()
        messages.success(request, f"Merged perfume into {target}.")
        return redirect("assistant_core:catalog_cleanup")


class BrandWatchProfileCreateView(StaffAssistantMixin, CreateView):
    model = models.BrandWatchProfile
    form_class = forms.BrandWatchProfileForm
    template_name = "assistant_core/form.html"
    success_url = reverse_lazy("assistant_core:brand_manager_list")


class BrandWatchProfileDetailView(StaffAssistantMixin, DetailView):
    model = models.BrandWatchProfile
    template_name = "assistant_core/brand_managers/detail.html"
    context_object_name = "profile"


class RunMockBrandWatchView(StaffAssistantMixin, View):
    def post(self, request, pk):
        job = run_mock_brand_watch(pk)
        messages.success(request, job.result_summary)
        return redirect("assistant_core:brand_manager_detail", pk=pk)


class ResearchJobListView(StaffAssistantMixin, ListView):
    model = models.ResearchJob
    template_name = "assistant_core/research/jobs.html"
    context_object_name = "jobs"
    paginate_by = 50


class ResearchJobDetailView(StaffAssistantMixin, DetailView):
    model = models.ResearchJob
    template_name = "assistant_core/research/job_detail.html"
    context_object_name = "job"


class DetectedChangeStatusView(StaffAssistantMixin, View):
    def post(self, request, pk, status):
        change = get_object_or_404(models.DetectedChange, pk=pk)
        if status in dict(models.DetectedChange.STATUS_CHOICES):
            change.status = status
            change.resolved_by = request.user
            change.resolved_at = timezone.now()
            change.save(update_fields=["status", "resolved_by", "resolved_at"])
        return redirect(request.POST.get("next") or "assistant_core:research_jobs")


class ClaimListView(StaffAssistantMixin, ListView):
    model = FactClaim
    template_name = "assistant_core/research/claims.html"
    context_object_name = "claims"
    paginate_by = 50


class ClaimStatusView(StaffAssistantMixin, View):
    def post(self, request, pk, status):
        claim = get_object_or_404(FactClaim, pk=pk)
        if status in dict(FactClaim.STATUS_CHOICES):
            claim.status = status
            claim.reviewed_by = request.user
            claim.reviewed_at = timezone.now()
            claim.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        return redirect("assistant_core:claims")


class DraftListView(StaffAssistantMixin, ListView):
    model = AIDraft
    template_name = "assistant_core/research/drafts.html"
    context_object_name = "drafts"
    paginate_by = 50


class DraftStatusView(StaffAssistantMixin, View):
    def post(self, request, pk, status):
        draft = get_object_or_404(AIDraft, pk=pk)
        if status in dict(AIDraft.STATUS_CHOICES):
            draft.status = status
            if status == AIDraft.STATUS_APPROVED:
                draft.approved_by = request.user
                draft.approved_at = timezone.now()
            draft.save()
        return redirect("assistant_core:drafts")


class PerfumeResearchView(StaffAssistantMixin, DetailView):
    model = Perfume
    template_name = "assistant_core/research/perfume.html"
    context_object_name = "perfume"


class GenerateDraftView(StaffAssistantMixin, View):
    def post(self, request, perfume_id):
        create_mock_draft(perfume_id)
        messages.success(request, "Pending draft generated from approved claims.")
        return redirect("assistant_core:perfume_research", pk=perfume_id)
