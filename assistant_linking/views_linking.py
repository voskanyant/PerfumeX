from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView, View

from assistant_linking import models
from assistant_linking.services.ai_recommendations import (
    ai_recommendation_queue_queryset,
    ai_recommendation_workbench_url,
    apply_ai_learning_proposal,
    apply_ai_recommendation_review,
    apply_ready_alias_learning_proposals,
    build_ai_proposal_quality_checks,
    build_ai_recommendation_queue_context,
    generate_manual_alias_recommendations,
    regenerate_alias_learning_proposal_preview,
    refresh_ai_learning_proposal_parses,
    revert_ai_learning_proposal_alias,
    safe_ai_recommendation_queue_redirect_url,
)
from assistant_linking.services.group_actions import (
    apply_group_action,
    rebuild_group_memberships,
)
from assistant_linking.services.group_queries import (
    build_group_detail_context,
    group_queue_queryset,
)
from assistant_linking.services.link_actions import (
    build_bulk_link_accepted_payload,
    build_bulk_link_selection,
    build_bulk_link_status_payload,
    build_undo_link_payload,
    bulk_link_products,
    bulk_link_success_message,
    get_undoable_bulk_link_action,
    is_ajax_request,
    latest_undoable_action,
    should_return_bulk_link_async_response,
    undo_link_action,
    undo_link_success_message,
)
from assistant_linking.services.suggestions import generate_suggestions_for_product
from assistant_linking.services.workbench import build_product_workbench_context
from assistant_linking.view_mixins import StaffAssistantMixin
from prices.models import SupplierProduct


class GroupQueueView(StaffAssistantMixin, ListView):
    model = models.MatchGroup
    template_name = "assistant_linking/groups/queue.html"
    context_object_name = "groups"
    paginate_by = 50

    def get_queryset(self):
        return group_queue_queryset(
            status=self.request.GET.get("status", ""),
            brand=self.request.GET.get("brand", ""),
        )

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "last_link_action": latest_undoable_action(self.request.user),
        }


class AIRecommendationQueueView(StaffAssistantMixin, ListView):
    model = models.AIRecommendation
    template_name = "assistant_linking/ai_recommendations/queue.html"
    context_object_name = "recommendations"
    paginate_by = 50

    def get_queryset(self):
        return ai_recommendation_queue_queryset(
            status=self.request.GET.get("status", ""),
            task_type=self.request.GET.get("task", ""),
            proposal_status=self.request.GET.get("proposal", ""),
            proposal_type=self.request.GET.get("proposal_type", ""),
            workflow=self.request.GET.get("workflow", ""),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_ai_recommendation_queue_context(self.request))
        visible_ready_alias_proposal_ids = []
        for recommendation in context["recommendations"]:
            recommendation.workbench_url = ai_recommendation_workbench_url(
                recommendation
            )
            proposal = getattr(recommendation, "learning_proposal", None)
            if (
                proposal
                and recommendation.status == models.AIRecommendation.STATUS_ACCEPTED
                and proposal.status == models.AILearningProposal.STATUS_PENDING
                and proposal.proposal_type
                in {
                    models.AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
                    models.AILearningProposal.PROPOSAL_BRAND_ALIAS,
                }
            ):
                visible_ready_alias_proposal_ids.append(proposal.pk)
        context["visible_ready_alias_proposal_ids"] = visible_ready_alias_proposal_ids
        return context


class AIRecommendationDetailView(StaffAssistantMixin, DetailView):
    model = models.AIRecommendation
    template_name = "assistant_linking/ai_recommendations/detail.html"
    context_object_name = "recommendation"
    pk_url_kwarg = "recommendation_id"

    def get_queryset(self):
        return models.AIRecommendation.objects.select_related(
            "supplier_product",
            "parsed_product",
            "fragrantica_product",
            "perfume",
            "perfume__brand",
            "reviewed_by",
            "learning_proposal",
            "learning_proposal__reviewed_by",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        recommendation = self.object
        proposal = getattr(recommendation, "learning_proposal", None)
        context["workbench_url"] = ai_recommendation_workbench_url(recommendation)
        context["proposal_quality_checks"] = build_ai_proposal_quality_checks(
            recommendation
        )
        context["proposal_can_refresh"] = bool(
            proposal
            and proposal.status == models.AILearningProposal.STATUS_APPLIED
            and proposal.proposal_type
            in {
                models.AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
                models.AILearningProposal.PROPOSAL_BRAND_ALIAS,
            }
        )
        context["proposal_can_revert_alias"] = bool(
            proposal
            and proposal.status == models.AILearningProposal.STATUS_APPLIED
            and proposal.proposal_type
            in {
                models.AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
                models.AILearningProposal.PROPOSAL_BRAND_ALIAS,
            }
        )
        context["proposal_can_regenerate_preview"] = bool(
            proposal
            and proposal.status == models.AILearningProposal.STATUS_PENDING
            and proposal.proposal_type
            in {
                models.AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
                models.AILearningProposal.PROPOSAL_BRAND_ALIAS,
            }
        )
        return context


class AIRecommendationReviewView(StaffAssistantMixin, View):
    def post(self, request, recommendation_id):
        redirect_url = safe_ai_recommendation_queue_redirect_url(
            request.POST.get("next", ""),
            host=request.get_host(),
        )
        try:
            recommendation = apply_ai_recommendation_review(
                recommendation_id=recommendation_id,
                action=request.POST.get("action", ""),
                user=request.user,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url)
        messages.success(request, f"AI recommendation marked {recommendation.status}.")
        return redirect(redirect_url)


class AIRecommendationPatternScanView(StaffAssistantMixin, View):
    def post(self, request):
        result = generate_manual_alias_recommendations()
        messages.success(request, result.message)
        return redirect(
            safe_ai_recommendation_queue_redirect_url(
                request.POST.get("next", ""),
                host=request.get_host(),
            )
        )


class AILearningProposalApplyView(StaffAssistantMixin, View):
    def post(self, request, proposal_id):
        redirect_url = safe_ai_recommendation_queue_redirect_url(
            request.POST.get("next", ""),
            host=request.get_host(),
        )
        try:
            _proposal, level, message = apply_ai_learning_proposal(
                proposal_id=proposal_id,
                user=request.user,
                host=request.get_host(),
                next_url=redirect_url,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url)

        if level == "success":
            messages.success(request, message)
        else:
            messages.error(request, message)
        return redirect(redirect_url)


class AILearningProposalBulkApplyAliasView(StaffAssistantMixin, View):
    def post(self, request):
        redirect_url = safe_ai_recommendation_queue_redirect_url(
            request.POST.get("next", ""),
            host=request.get_host(),
        )
        result = apply_ready_alias_learning_proposals(
            proposal_ids=request.POST.getlist("proposal_ids"),
            user=request.user,
        )
        if result.failed:
            messages.error(request, result.message)
        else:
            messages.success(request, result.message)
        return redirect(redirect_url)


class AILearningProposalRefreshParsesView(StaffAssistantMixin, View):
    def post(self, request, proposal_id):
        redirect_url = safe_ai_recommendation_queue_redirect_url(
            request.POST.get("next", ""),
            host=request.get_host(),
        )
        try:
            _proposal, level, message = refresh_ai_learning_proposal_parses(
                proposal_id=proposal_id,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url)

        if level == "success":
            messages.success(request, message)
        else:
            messages.error(request, message)
        return redirect(redirect_url)


class AILearningProposalRegeneratePreviewView(StaffAssistantMixin, View):
    def post(self, request, proposal_id):
        redirect_url = safe_ai_recommendation_queue_redirect_url(
            request.POST.get("next", ""),
            host=request.get_host(),
        )
        try:
            _proposal, level, message = regenerate_alias_learning_proposal_preview(
                proposal_id=proposal_id,
                user=request.user,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url)

        if level == "success":
            messages.success(request, message)
        else:
            messages.error(request, message)
        return redirect(redirect_url)


class AILearningProposalRevertAliasView(StaffAssistantMixin, View):
    def post(self, request, proposal_id):
        redirect_url = safe_ai_recommendation_queue_redirect_url(
            request.POST.get("next", ""),
            host=request.get_host(),
        )
        try:
            _proposal, level, message = revert_ai_learning_proposal_alias(
                proposal_id=proposal_id,
                user=request.user,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url)

        if level == "success":
            messages.success(request, message)
        else:
            messages.error(request, message)
        return redirect(redirect_url)


class GroupDetailView(StaffAssistantMixin, DetailView):
    model = models.MatchGroup
    template_name = "assistant_linking/groups/detail.html"
    context_object_name = "group"
    pk_url_kwarg = "group_id"

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            **build_group_detail_context(
                group=self.object,
                user=self.request.user,
            ),
        }


class RebuildGroupsView(StaffAssistantMixin, View):
    def post(self, request):
        result = rebuild_group_memberships(
            only_open=request.POST.get("only_open") == "1"
        )
        messages.success(request, result.message)
        return redirect("assistant_linking:group_queue")


class GroupActionView(StaffAssistantMixin, View):
    def post(self, request, group_id, action):
        apply_group_action(
            group_id=group_id,
            action=action,
            item_ids=request.POST.getlist("item_ids"),
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, "Group action applied.")
        return redirect("assistant_linking:group_detail", group_id=group_id)


class ProductWorkbenchView(StaffAssistantMixin, DetailView):
    model = SupplierProduct
    template_name = "assistant_linking/workbench/product.html"
    context_object_name = "product"
    pk_url_kwarg = "supplier_product_id"

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            **build_product_workbench_context(
                product=self.object,
                user=self.request.user,
            ),
        }


class GenerateSuggestionsView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        result = generate_suggestions_for_product(supplier_product_id)
        messages.success(request, result.message)
        return redirect(
            "assistant_linking:product_workbench",
            supplier_product_id=supplier_product_id,
        )


class BulkLinkView(StaffAssistantMixin, View):
    def post(self, request, supplier_product_id):
        selection = build_bulk_link_selection(
            supplier_product_id=supplier_product_id,
            post_data=request.POST,
        )
        if selection.has_error and selection.error_status:
            return HttpResponse(selection.error_message, status=selection.error_status)
        if selection.has_error:
            messages.error(request, selection.error_message)
            return redirect(
                "assistant_linking:product_workbench",
                supplier_product_id=supplier_product_id,
            )

        action = bulk_link_products(
            user=request.user,
            product_ids=selection.product_ids,
            perfume_id=int(selection.perfume_id) if selection.perfume_id else None,
            variant_id=int(selection.variant_id) if selection.variant_id else None,
            allow_overwrite=selection.allow_overwrite,
            apply_to_similar=selection.apply_to_similar,
            reason=request.POST.get("reason", ""),
        )
        if should_return_bulk_link_async_response(
            product_ids=selection.product_ids,
            headers=request.headers,
        ):
            return JsonResponse(
                build_bulk_link_accepted_payload(
                    action,
                    status_url=reverse_lazy(
                        "assistant_linking:bulk_link_status",
                        kwargs={"action_id": action.id},
                    ),
                    undo_url=reverse_lazy(
                        "assistant_linking:undo_link_action",
                        kwargs={"action_id": action.id},
                    ),
                ),
                status=202,
            )
        messages.success(request, bulk_link_success_message(action))
        return redirect(
            "assistant_linking:product_workbench",
            supplier_product_id=supplier_product_id,
        )


class BulkLinkStatusView(StaffAssistantMixin, View):
    def get(self, request, action_id):
        action = get_object_or_404(models.LinkAction, pk=action_id, user=request.user)
        return JsonResponse(
            build_bulk_link_status_payload(
                action,
                undo_url=reverse_lazy(
                    "assistant_linking:undo_link_action",
                    kwargs={"action_id": action.id},
                ),
            )
        )


class UndoLinkActionView(StaffAssistantMixin, View):
    def post(self, request, action_id):
        action = get_undoable_bulk_link_action(
            action_id=action_id,
            user=request.user,
        )
        restored = undo_link_action(action, request.user)
        if is_ajax_request(request.headers):
            return JsonResponse(build_undo_link_payload(restored))
        messages.success(request, undo_link_success_message(restored))
        return redirect(request.POST.get("next") or "assistant_linking:group_queue")
