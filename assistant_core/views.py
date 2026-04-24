from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView, View

from assistant_core import forms, models
from assistant_core.services.mock_brand_research import run_mock_brand_watch
from assistant_core.services.mock_description_generator import create_mock_draft
from catalog.models import AIDraft, FactClaim, Perfume, Source


class StaffAssistantMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return bool(self.request.user and self.request.user.is_staff)


class DashboardView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/dashboard.html"

    def get_context_data(self, **kwargs):
        from assistant_linking.models import LinkSuggestion, ManualLinkDecision, ParsedSupplierProduct

        return {
            **super().get_context_data(**kwargs),
            "cards": [
                ("Normalisation", "assistant_linking:normalization_dashboard", ParsedSupplierProduct.objects.filter(confidence__lt=75).count()),
                ("Linking Workbench", "assistant_linking:group_queue", LinkSuggestion.objects.filter(status=LinkSuggestion.STATUS_PENDING).count()),
                ("Knowledge Base", "assistant_core:knowledge", models.KnowledgeNote.objects.filter(active=True).count()),
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
        return {
            **super().get_context_data(**kwargs),
            "global_rules": models.GlobalRule.objects.order_by("priority", "title"),
            "supplier_rules": models.SupplierRule.objects.select_related("supplier", "brand").order_by("supplier__name", "priority"),
            "notes": models.KnowledgeNote.objects.select_related("supplier", "brand", "perfume").order_by("category", "title"),
        }


class RulesView(KnowledgeView):
    pass


class AliasesView(StaffAssistantMixin, TemplateView):
    template_name = "assistant_core/knowledge/aliases.html"

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
