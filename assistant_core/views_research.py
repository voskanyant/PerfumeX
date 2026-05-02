from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, View

from assistant_core import forms, models
from assistant_core.services.research_actions import (
    generate_mock_draft_action,
    run_mock_brand_watch_action,
)
from assistant_core.services.review_actions import (
    update_ai_draft_status,
    update_detected_change_status,
    update_fact_claim_status,
)
from assistant_core.view_mixins import StaffAssistantMixin
from catalog.models import AIDraft, FactClaim, Perfume


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
        action = run_mock_brand_watch_action(pk)
        messages.success(request, action.message)
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
        update_detected_change_status(change, status, request.user)
        return redirect(request.POST.get("next") or "assistant_core:research_jobs")


class ClaimListView(StaffAssistantMixin, ListView):
    model = FactClaim
    template_name = "assistant_core/research/claims.html"
    context_object_name = "claims"
    paginate_by = 50


class ClaimStatusView(StaffAssistantMixin, View):
    def post(self, request, pk, status):
        claim = get_object_or_404(FactClaim, pk=pk)
        update_fact_claim_status(claim, status, request.user)
        return redirect("assistant_core:claims")


class DraftListView(StaffAssistantMixin, ListView):
    model = AIDraft
    template_name = "assistant_core/research/drafts.html"
    context_object_name = "drafts"
    paginate_by = 50


class DraftStatusView(StaffAssistantMixin, View):
    def post(self, request, pk, status):
        draft = get_object_or_404(AIDraft, pk=pk)
        update_ai_draft_status(draft, status, request.user)
        return redirect("assistant_core:drafts")


class PerfumeResearchView(StaffAssistantMixin, DetailView):
    model = Perfume
    template_name = "assistant_core/research/perfume.html"
    context_object_name = "perfume"


class GenerateDraftView(StaffAssistantMixin, View):
    def post(self, request, perfume_id):
        action = generate_mock_draft_action(perfume_id)
        messages.success(request, action.message)
        return redirect("assistant_core:perfume_research", pk=perfume_id)
