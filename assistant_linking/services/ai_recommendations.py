from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from assistant_linking.models import AIRecommendation, AILearningProposal
from assistant_linking.models import BrandAlias
from assistant_linking.models import ManualLinkDecision, ParsedSupplierProduct
from assistant_linking.models import ProductAlias
from assistant_linking.utils.text import normalize_alias_value
from catalog.models import Brand


AI_RECOMMENDATION_STATUS_FILTERS = {
    "all",
    AIRecommendation.STATUS_PENDING,
    AIRecommendation.STATUS_ACCEPTED,
    AIRecommendation.STATUS_REJECTED,
    AIRecommendation.STATUS_SUPERSEDED,
}
AI_PROPOSAL_STATUS_NONE = "none"
AI_PROPOSAL_STATUS_FILTERS = {
    "all",
    AI_PROPOSAL_STATUS_NONE,
    AILearningProposal.STATUS_PENDING,
    AILearningProposal.STATUS_APPROVED,
    AILearningProposal.STATUS_REJECTED,
    AILearningProposal.STATUS_APPLIED,
    AILearningProposal.STATUS_REVERTED,
}
AI_PROPOSAL_TYPE_FILTERS = {
    "all",
    "none",
    *{choice[0] for choice in AILearningProposal.PROPOSAL_CHOICES},
}
AI_WORKFLOW_FILTER_ALL = "all"
AI_WORKFLOW_FILTER_NEEDS_REVIEW = "needs_review"
AI_WORKFLOW_FILTER_READY_APPLY = "ready_apply"
AI_WORKFLOW_FILTER_APPLIED = "applied"
AI_WORKFLOW_FILTERS = {
    AI_WORKFLOW_FILTER_ALL,
    AI_WORKFLOW_FILTER_NEEDS_REVIEW,
    AI_WORKFLOW_FILTER_READY_APPLY,
    AI_WORKFLOW_FILTER_APPLIED,
}
AI_BULK_APPLY_ALIAS_PROPOSAL_TYPES = {
    AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
    AILearningProposal.PROPOSAL_BRAND_ALIAS,
}
MANUAL_PATTERN_PROMPT_VERSION = "manual-pattern-v1"
MANUAL_PATTERN_MODEL_NAME = "deterministic-pattern-v1"
BRAND_ALIAS_BLOCKLIST = {
    "atelier",
    "beauty",
    "collection",
    "exclusive",
    "fragrance",
    "fragrances",
    "inc",
    "leather",
    "ltd",
    "oud",
    "parfum",
    "parfums",
    "perfume",
    "perfumes",
    "prive",
    "privee",
}


@dataclass(frozen=True)
class PatternScanResult:
    created: int
    skipped_existing: int
    scanned_decisions: int

    @property
    def message(self) -> str:
        return (
            f"Created {self.created} recommendation(s) from repeated manual "
            f"decisions; skipped {self.skipped_existing} existing pattern(s)."
        )


@dataclass(frozen=True)
class ProductAliasImpact:
    saved_parse_matches: int
    active_supplier_matches: int
    unlocked_parse_matches: int
    already_linked_to_target: int
    sample_supplier_products: list[str]

    def as_json(self) -> dict:
        return {
            "saved_parse_matches": self.saved_parse_matches,
            "active_supplier_matches": self.active_supplier_matches,
            "unlocked_parse_matches": self.unlocked_parse_matches,
            "already_linked_to_target": self.already_linked_to_target,
            "sample_supplier_products": self.sample_supplier_products,
        }


@dataclass(frozen=True)
class BrandAliasImpact:
    saved_parse_matches: int
    active_supplier_matches: int
    unlocked_parse_matches: int
    already_brand_target: int
    sample_supplier_products: list[str]

    def as_json(self) -> dict:
        return {
            "saved_parse_matches": self.saved_parse_matches,
            "active_supplier_matches": self.active_supplier_matches,
            "unlocked_parse_matches": self.unlocked_parse_matches,
            "already_brand_target": self.already_brand_target,
            "sample_supplier_products": self.sample_supplier_products,
        }


@dataclass(frozen=True)
class ProductAliasRefreshResult:
    refreshed: int
    skipped_locked: int
    matched_before_refresh: int

    @property
    def message(self) -> str:
        if not self.matched_before_refresh:
            return "No saved parses still match this product alias proposal."
        return (
            f"Refreshed {self.refreshed} affected saved parse(s); "
            f"skipped {self.skipped_locked} human-locked parse(s)."
        )


@dataclass(frozen=True)
class BrandAliasRefreshResult:
    refreshed: int
    skipped_locked: int
    matched_before_refresh: int

    @property
    def message(self) -> str:
        if not self.matched_before_refresh:
            return "No saved parses still match this brand alias proposal."
        return (
            f"Refreshed {self.refreshed} affected saved parse(s); "
            f"skipped {self.skipped_locked} human-locked parse(s)."
        )


@dataclass(frozen=True)
class BulkAliasApplyResult:
    requested: int
    applied: int
    skipped: int
    failed: int
    messages: list[str]

    @property
    def message(self) -> str:
        parts = [f"Applied {self.applied} accepted alias proposal(s)."]
        if self.skipped:
            parts.append(f"Skipped {self.skipped} proposal(s).")
        if self.failed:
            parts.append(f"{self.failed} proposal(s) failed.")
        return " ".join(parts)


def normalize_ai_recommendation_status(value: str | None) -> str:
    status = (value or AIRecommendation.STATUS_PENDING).strip()
    return (
        status
        if status in AI_RECOMMENDATION_STATUS_FILTERS
        else AIRecommendation.STATUS_PENDING
    )


def normalize_ai_proposal_status(value: str | None) -> str:
    status = (value or "all").strip()
    return status if status in AI_PROPOSAL_STATUS_FILTERS else "all"


def normalize_ai_proposal_type(value: str | None) -> str:
    proposal_type = (value or "all").strip()
    return proposal_type if proposal_type in AI_PROPOSAL_TYPE_FILTERS else "all"


def normalize_ai_workflow_filter(value: str | None) -> str:
    workflow = (value or AI_WORKFLOW_FILTER_ALL).strip()
    return workflow if workflow in AI_WORKFLOW_FILTERS else AI_WORKFLOW_FILTER_ALL


def ai_recommendation_queue_queryset(
    *,
    status: str = "",
    task_type: str = "",
    proposal_status: str = "",
    proposal_type: str = "",
    workflow: str = "",
):
    queryset = AIRecommendation.objects.select_related(
        "supplier_product",
        "parsed_product",
        "fragrantica_product",
        "perfume",
        "perfume__brand",
        "reviewed_by",
        "learning_proposal",
    )
    status_filter = normalize_ai_recommendation_status(status)
    if status_filter != "all":
        queryset = queryset.filter(status=status_filter)
    if task_type:
        valid_task_types = {choice[0] for choice in AIRecommendation.TASK_CHOICES}
        if task_type in valid_task_types:
            queryset = queryset.filter(task_type=task_type)
    proposal_filter = normalize_ai_proposal_status(proposal_status)
    if proposal_filter == AI_PROPOSAL_STATUS_NONE:
        queryset = queryset.filter(learning_proposal__isnull=True)
    elif proposal_filter != "all":
        queryset = queryset.filter(learning_proposal__status=proposal_filter)
    proposal_type_filter = normalize_ai_proposal_type(proposal_type)
    if proposal_type_filter == "none":
        queryset = queryset.filter(learning_proposal__isnull=True)
    elif proposal_type_filter != "all":
        queryset = queryset.filter(
            learning_proposal__proposal_type=proposal_type_filter
        )
    workflow_filter = normalize_ai_workflow_filter(workflow)
    if workflow_filter == AI_WORKFLOW_FILTER_NEEDS_REVIEW:
        queryset = queryset.filter(status=AIRecommendation.STATUS_PENDING)
    elif workflow_filter == AI_WORKFLOW_FILTER_READY_APPLY:
        queryset = queryset.filter(
            status=AIRecommendation.STATUS_ACCEPTED,
            learning_proposal__status=AILearningProposal.STATUS_PENDING,
        )
    elif workflow_filter == AI_WORKFLOW_FILTER_APPLIED:
        queryset = queryset.filter(
            learning_proposal__status=AILearningProposal.STATUS_APPLIED
        )
    return queryset.order_by("-created_at", "-id")


def build_ai_recommendation_queue_context(request) -> dict:
    status_filter = normalize_ai_recommendation_status(request.GET.get("status"))
    task_filter = request.GET.get("task", "").strip()
    proposal_filter = normalize_ai_proposal_status(request.GET.get("proposal"))
    proposal_type_filter = normalize_ai_proposal_type(request.GET.get("proposal_type"))
    workflow_filter = normalize_ai_workflow_filter(request.GET.get("workflow"))
    counts = AIRecommendation.objects.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=AIRecommendation.STATUS_PENDING)),
        accepted=Count("id", filter=Q(status=AIRecommendation.STATUS_ACCEPTED)),
        ready_apply=Count(
            "id",
            filter=Q(
                status=AIRecommendation.STATUS_ACCEPTED,
                learning_proposal__status=AILearningProposal.STATUS_PENDING,
            ),
        ),
        pending_proposals=Count(
            "id",
            filter=Q(learning_proposal__status=AILearningProposal.STATUS_PENDING),
        ),
        applied_proposals=Count(
            "id",
            filter=Q(learning_proposal__status=AILearningProposal.STATUS_APPLIED),
        ),
    )
    return {
        "status_filter": status_filter,
        "task_filter": task_filter,
        "proposal_filter": proposal_filter,
        "proposal_type_filter": proposal_type_filter,
        "workflow_filter": workflow_filter,
        "status_choices": [("all", "All"), *AIRecommendation.STATUS_CHOICES],
        "task_choices": AIRecommendation.TASK_CHOICES,
        "proposal_choices": [
            ("all", "All proposals"),
            (AI_PROPOSAL_STATUS_NONE, "No proposal"),
            *AILearningProposal.STATUS_CHOICES,
        ],
        "proposal_type_choices": [
            ("all", "All proposal types"),
            ("none", "No proposal"),
            *AILearningProposal.PROPOSAL_CHOICES,
        ],
        "workflow_choices": [
            (AI_WORKFLOW_FILTER_ALL, "All workflow states"),
            (AI_WORKFLOW_FILTER_NEEDS_REVIEW, "Needs review"),
            (AI_WORKFLOW_FILTER_READY_APPLY, "Ready to apply"),
            (AI_WORKFLOW_FILTER_APPLIED, "Applied"),
        ],
        "queue_counts": counts,
    }


def build_ai_proposal_quality_checks(recommendation: AIRecommendation) -> list[dict]:
    try:
        proposal = recommendation.learning_proposal
    except AILearningProposal.DoesNotExist:
        return [
            {
                "label": "Learning proposal",
                "status": "Not created",
                "detail": "Accept the recommendation first when this advice supports a proposal.",
                "tone": "muted",
            }
        ]

    checks = [
        {
            "label": "Recommendation review",
            "status": (
                "Accepted"
                if recommendation.status == AIRecommendation.STATUS_ACCEPTED
                else recommendation.get_status_display()
            ),
            "detail": "A proposal can apply only after the recommendation is accepted.",
            "tone": (
                "ok"
                if recommendation.status == AIRecommendation.STATUS_ACCEPTED
                else "warning"
            ),
        },
        {
            "label": "Proposal status",
            "status": proposal.get_status_display(),
            "detail": "Pending proposals are ready for an explicit apply action.",
            "tone": (
                "ok"
                if proposal.status == AILearningProposal.STATUS_PENDING
                else "muted"
            ),
        },
    ]
    action = proposal.proposed_action_json or {}
    impact = proposal.impact_json or {}
    preview = impact.get("preview") if isinstance(impact, dict) else None
    evidence = proposal.evidence_json or {}
    if proposal.proposal_type == AILearningProposal.PROPOSAL_FRAGRANTICA_LINK_REVIEW:
        has_target = bool(
            action.get("perfume_id") and action.get("fragrantica_product_id")
        )
        checks.extend(
            [
                {
                    "label": "Link target",
                    "status": "Ready" if has_target else "Missing",
                    "detail": "Requires both a local perfume and a staged Fragrantica row.",
                    "tone": "ok" if has_target else "warning",
                },
                {
                    "label": "Apply behavior",
                    "status": "Manual link",
                    "detail": "Applying reuses the reviewed Fragrantica link action; concentration remains local.",
                    "tone": "ok",
                },
            ]
        )
    elif proposal.proposal_type in AI_BULK_APPLY_ALIAS_PROPOSAL_TYPES:
        has_alias_target = bool(
            action.get("alias_text") and action.get("canonical_text")
        )
        unlocked = (preview or {}).get("unlocked_parse_matches", 0)
        current_preview = _current_alias_proposal_preview(proposal)
        preview_changed = _proposal_preview_changed(preview, current_preview)
        checks.extend(
            [
                {
                    "label": "Alias target",
                    "status": "Ready" if has_alias_target else "Missing",
                    "detail": "Requires alias text and canonical target text.",
                    "tone": "ok" if has_alias_target else "warning",
                },
                {
                    "label": "Impact preview",
                    "status": f"{unlocked} unlocked" if preview else "Missing",
                    "detail": "Alias apply creates knowledge only; parse refresh stays explicit and skips human locks.",
                    "tone": "ok" if preview else "warning",
                },
            ]
        )
        if preview and current_preview:
            checks.append(
                {
                    "label": "Preview freshness",
                    "status": "Changed" if preview_changed else "Current",
                    "detail": (
                        "Current matching rows differ from the saved preview; review the impact again before apply."
                        if preview_changed
                        else "Current matching row counts still match the saved impact preview."
                    ),
                    "tone": "warning" if preview_changed else "ok",
                }
            )
    if evidence.get("decision_count"):
        checks.append(
            {
                "label": "Reviewed decisions",
                "status": str(evidence["decision_count"]),
                "detail": "Repeated manual link decisions support this proposal.",
                "tone": "ok",
            }
        )
    if recommendation.risk_level == AIRecommendation.RISK_HIGH:
        checks.append(
            {
                "label": "Risk",
                "status": "High",
                "detail": "High-risk advice should be checked carefully before apply.",
                "tone": "warning",
            }
        )
    return checks


def _current_alias_proposal_preview(proposal: AILearningProposal) -> dict | None:
    action = proposal.proposed_action_json or {}
    if proposal.proposal_type == AILearningProposal.PROPOSAL_PRODUCT_ALIAS:
        return build_product_alias_impact_preview(action, sample_limit=0).as_json()
    if proposal.proposal_type == AILearningProposal.PROPOSAL_BRAND_ALIAS:
        return build_brand_alias_impact_preview(action, sample_limit=0).as_json()
    return None


def _proposal_preview_changed(
    stored_preview: dict | None,
    current_preview: dict | None,
) -> bool:
    if not stored_preview or not current_preview:
        return False
    comparable_keys = (
        "saved_parse_matches",
        "active_supplier_matches",
        "unlocked_parse_matches",
    )
    return any(
        (stored_preview.get(key) or 0) != (current_preview.get(key) or 0)
        for key in comparable_keys
    )


def _ensure_alias_proposal_preview_current(proposal: AILearningProposal) -> None:
    impact = proposal.impact_json or {}
    stored_preview = impact.get("preview") if isinstance(impact, dict) else None
    current_preview = _current_alias_proposal_preview(proposal)
    if not stored_preview or not current_preview:
        raise ValueError(
            "Alias proposal impact preview is missing; regenerate or review the proposal before applying."
        )
    if _proposal_preview_changed(stored_preview, current_preview):
        raise ValueError(
            "Alias proposal impact preview changed; regenerate or review the proposal before applying."
        )


def _stable_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _product_alias_exists(*, brand_id, perfume_id, alias_text, supplier_id):
    queryset = ProductAlias.objects.filter(
        active=True,
        brand_id=brand_id,
        perfume_id=perfume_id,
        alias_text__iexact=alias_text,
    )
    if supplier_id:
        queryset = queryset.filter(
            Q(supplier_id=supplier_id) | Q(supplier__isnull=True)
        )
    else:
        queryset = queryset.filter(supplier__isnull=True)
    return queryset.exists()


def _brand_alias_exists(*, brand_id, alias_text, supplier_id):
    queryset = BrandAlias.objects.filter(
        active=True,
        brand_id=brand_id,
        alias_text__iexact=alias_text,
    )
    if supplier_id:
        queryset = queryset.filter(
            Q(supplier_id=supplier_id) | Q(supplier__isnull=True)
        )
    else:
        queryset = queryset.filter(supplier__isnull=True)
    return queryset.exists()


def _brand_alias_text_is_safe(alias_text: str, *, target_brand_id: int) -> bool:
    normalized = normalize_alias_value(alias_text)
    if len(normalized) < 3 or normalized in BRAND_ALIAS_BLOCKLIST:
        return False
    if normalized.isdigit():
        return False
    conflicting_brand = (
        Brand.objects.filter(is_active=True)
        .exclude(pk=target_brand_id)
        .filter(name__iexact=alias_text)
        .exists()
    )
    return not conflicting_brand


def _manual_decision_alias_groups(*, max_decisions: int):
    decisions = (
        ManualLinkDecision.objects.filter(
            decision_type__in=[
                ManualLinkDecision.DECISION_APPROVE_PERFUME,
                ManualLinkDecision.DECISION_APPROVE_VARIANT,
            ],
            perfume__isnull=False,
            supplier_product__assistant_parse__isnull=False,
        )
        .select_related(
            "perfume",
            "perfume__brand",
            "supplier_product",
            "supplier_product__supplier",
            "supplier_product__assistant_parse",
        )
        .order_by("-created_at", "-id")[:max_decisions]
    )
    groups: dict[tuple[int, int, str], list[ManualLinkDecision]] = {}
    scanned = 0
    for decision in decisions:
        scanned += 1
        parsed = decision.supplier_product.assistant_parse
        perfume = decision.perfume
        alias_text = (parsed.product_name_text or "").strip()
        canonical_text = (perfume.name or "").strip()
        if not alias_text or not canonical_text:
            continue
        if (
            parsed.normalized_brand_id
            and parsed.normalized_brand_id != perfume.brand_id
        ):
            continue
        normalized_alias = normalize_alias_value(alias_text)
        normalized_canonical = normalize_alias_value(canonical_text)
        if normalized_alias == normalized_canonical:
            continue
        key = (perfume.brand_id, perfume.id, normalized_alias)
        groups.setdefault(key, []).append(decision)
    return groups, scanned


def _manual_decision_brand_alias_groups(*, max_decisions: int):
    decisions = (
        ManualLinkDecision.objects.filter(
            decision_type__in=[
                ManualLinkDecision.DECISION_APPROVE_PERFUME,
                ManualLinkDecision.DECISION_APPROVE_VARIANT,
            ],
            perfume__isnull=False,
            supplier_product__assistant_parse__isnull=False,
        )
        .select_related(
            "perfume",
            "perfume__brand",
            "supplier_product",
            "supplier_product__supplier",
            "supplier_product__assistant_parse",
        )
        .order_by("-created_at", "-id")[:max_decisions]
    )
    groups: dict[tuple[int, str], list[ManualLinkDecision]] = {}
    scanned = 0
    for decision in decisions:
        scanned += 1
        parsed = decision.supplier_product.assistant_parse
        target_brand = decision.perfume.brand
        alias_text = (parsed.detected_brand_text or "").strip()
        if not alias_text:
            continue
        if parsed.normalized_brand_id == target_brand.id:
            continue
        if not _brand_alias_text_is_safe(alias_text, target_brand_id=target_brand.id):
            continue
        normalized_alias = normalize_alias_value(alias_text)
        normalized_canonical = normalize_alias_value(target_brand.name)
        if normalized_alias == normalized_canonical:
            continue
        key = (target_brand.id, normalized_alias)
        groups.setdefault(key, []).append(decision)
    return groups, scanned


def generate_manual_product_alias_recommendations(
    *,
    min_count: int = 2,
    limit: int = 50,
    max_decisions: int = 2000,
) -> PatternScanResult:
    created = 0
    skipped_existing = 0
    groups, scanned = _manual_decision_alias_groups(max_decisions=max_decisions)
    for decisions in groups.values():
        if created >= limit:
            break
        if len(decisions) < min_count:
            continue
        representative = decisions[0]
        parsed: ParsedSupplierProduct = representative.supplier_product.assistant_parse
        perfume = representative.perfume
        alias_text = parsed.product_name_text.strip()
        supplier_ids = {
            decision.supplier_product.supplier_id
            for decision in decisions
            if decision.supplier_product_id
        }
        supplier_id = supplier_ids.pop() if len(supplier_ids) == 1 else None
        hash_payload = {
            "kind": "manual_product_alias_pattern",
            "version": 1,
            "brand_id": perfume.brand_id,
            "perfume_id": perfume.id,
            "alias": normalize_alias_value(alias_text),
            "supplier_id": supplier_id,
        }
        input_hash = _stable_hash(hash_payload)
        if AIRecommendation.objects.filter(
            task_type=AIRecommendation.TASK_KB_SUGGESTION,
            input_hash=input_hash,
        ).exists() or _product_alias_exists(
            brand_id=perfume.brand_id,
            perfume_id=perfume.id,
            alias_text=alias_text,
            supplier_id=supplier_id,
        ):
            skipped_existing += 1
            continue

        decision_ids = [decision.id for decision in decisions]
        supplier_names = sorted(
            {
                decision.supplier_product.supplier.name
                for decision in decisions
                if decision.supplier_product_id
            }
        )
        confidence = min(96, 80 + len(decisions) * 4)
        AIRecommendation.objects.create(
            task_type=AIRecommendation.TASK_KB_SUGGESTION,
            status=AIRecommendation.STATUS_PENDING,
            supplier_product=representative.supplier_product,
            parsed_product=parsed,
            perfume=perfume,
            input_hash=input_hash,
            prompt_version=MANUAL_PATTERN_PROMPT_VERSION,
            model_name=MANUAL_PATTERN_MODEL_NAME,
            confidence=confidence,
            risk_level=(
                AIRecommendation.RISK_LOW
                if supplier_id
                else AIRecommendation.RISK_MEDIUM
            ),
            input_context_json={
                "source": "manual_link_decisions",
                "decision_ids": decision_ids,
                "decision_count": len(decisions),
                "supplier_names": supplier_names,
            },
            recommendation_json={
                "proposal_type": AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
                "alias_text": alias_text,
                "canonical_text": perfume.name,
                "brand_id": perfume.brand_id,
                "brand_name": perfume.brand.name,
                "perfume_id": perfume.id,
                "supplier_id": supplier_id,
                "decision_count": len(decisions),
                "decision_ids": decision_ids,
                "supplier_names": supplier_names,
            },
            reasoning=(
                f"{len(decisions)} reviewed manual links mapped supplier text "
                f"'{alias_text}' to {perfume.brand.name} / {perfume.name}."
            ),
        )
        created += 1
    return PatternScanResult(
        created=created,
        skipped_existing=skipped_existing,
        scanned_decisions=scanned,
    )


def generate_manual_brand_alias_recommendations(
    *,
    min_count: int = 2,
    limit: int = 50,
    max_decisions: int = 2000,
) -> PatternScanResult:
    created = 0
    skipped_existing = 0
    groups, scanned = _manual_decision_brand_alias_groups(max_decisions=max_decisions)
    for decisions in groups.values():
        if created >= limit:
            break
        if len(decisions) < min_count:
            continue
        representative = decisions[0]
        parsed: ParsedSupplierProduct = representative.supplier_product.assistant_parse
        target_brand = representative.perfume.brand
        alias_text = parsed.detected_brand_text.strip()
        supplier_ids = {
            decision.supplier_product.supplier_id
            for decision in decisions
            if decision.supplier_product_id
        }
        supplier_id = supplier_ids.pop() if len(supplier_ids) == 1 else None
        hash_payload = {
            "kind": "manual_brand_alias_pattern",
            "version": 1,
            "brand_id": target_brand.id,
            "alias": normalize_alias_value(alias_text),
            "supplier_id": supplier_id,
        }
        input_hash = _stable_hash(hash_payload)
        if AIRecommendation.objects.filter(
            task_type=AIRecommendation.TASK_KB_SUGGESTION,
            input_hash=input_hash,
        ).exists() or _brand_alias_exists(
            brand_id=target_brand.id,
            alias_text=alias_text,
            supplier_id=supplier_id,
        ):
            skipped_existing += 1
            continue

        decision_ids = [decision.id for decision in decisions]
        supplier_names = sorted(
            {
                decision.supplier_product.supplier.name
                for decision in decisions
                if decision.supplier_product_id
            }
        )
        confidence = min(96, 80 + len(decisions) * 4)
        AIRecommendation.objects.create(
            task_type=AIRecommendation.TASK_KB_SUGGESTION,
            status=AIRecommendation.STATUS_PENDING,
            supplier_product=representative.supplier_product,
            parsed_product=parsed,
            perfume=representative.perfume,
            input_hash=input_hash,
            prompt_version=MANUAL_PATTERN_PROMPT_VERSION,
            model_name=MANUAL_PATTERN_MODEL_NAME,
            confidence=confidence,
            risk_level=(
                AIRecommendation.RISK_LOW
                if supplier_id
                else AIRecommendation.RISK_MEDIUM
            ),
            input_context_json={
                "source": "manual_link_decisions",
                "decision_ids": decision_ids,
                "decision_count": len(decisions),
                "supplier_names": supplier_names,
            },
            recommendation_json={
                "proposal_type": AILearningProposal.PROPOSAL_BRAND_ALIAS,
                "alias_text": alias_text,
                "canonical_text": target_brand.name,
                "brand_id": target_brand.id,
                "brand_name": target_brand.name,
                "supplier_id": supplier_id,
                "decision_count": len(decisions),
                "decision_ids": decision_ids,
                "supplier_names": supplier_names,
            },
            reasoning=(
                f"{len(decisions)} reviewed manual links mapped supplier brand text "
                f"'{alias_text}' to {target_brand.name}."
            ),
        )
        created += 1
    return PatternScanResult(
        created=created,
        skipped_existing=skipped_existing,
        scanned_decisions=scanned,
    )


def generate_manual_alias_recommendations(
    *,
    min_count: int = 2,
    limit: int = 50,
    max_decisions: int = 2000,
) -> PatternScanResult:
    product_result = generate_manual_product_alias_recommendations(
        min_count=min_count,
        limit=limit,
        max_decisions=max_decisions,
    )
    remaining_limit = max(0, limit - product_result.created)
    brand_result = (
        generate_manual_brand_alias_recommendations(
            min_count=min_count,
            limit=remaining_limit,
            max_decisions=max_decisions,
        )
        if remaining_limit
        else PatternScanResult(0, 0, product_result.scanned_decisions)
    )
    return PatternScanResult(
        created=product_result.created + brand_result.created,
        skipped_existing=(
            product_result.skipped_existing + brand_result.skipped_existing
        ),
        scanned_decisions=max(
            product_result.scanned_decisions,
            brand_result.scanned_decisions,
        ),
    )


def ai_recommendation_queue_url() -> str:
    return reverse("assistant_linking:ai_recommendation_queue")


def safe_ai_recommendation_queue_redirect_url(next_url: str, *, host: str = "") -> str:
    redirect_url = next_url or ai_recommendation_queue_url()
    if not url_has_allowed_host_and_scheme(
        redirect_url,
        allowed_hosts={host} if host else None,
    ):
        return ai_recommendation_queue_url()
    return redirect_url


def ai_recommendation_workbench_url(recommendation: AIRecommendation) -> str:
    if recommendation.perfume_id:
        return (
            reverse("prices:catalogue_linking_workbench")
            + f"?perfume={recommendation.perfume_id}"
        )
    if recommendation.supplier_product_id:
        return reverse(
            "assistant_linking:product_workbench",
            args=[recommendation.supplier_product_id],
        )
    return reverse("assistant_linking:ai_recommendation_queue")


def learning_proposal_for_recommendation(
    recommendation: AIRecommendation,
) -> AILearningProposal | None:
    return AILearningProposal.objects.filter(
        source_recommendation=recommendation
    ).first()


def build_product_alias_impact_preview(
    action: dict,
    *,
    sample_limit: int = 5,
) -> ProductAliasImpact:
    perfume_id = action.get("perfume_id")
    queryset = _product_alias_impact_queryset(action)
    if not perfume_id:
        return ProductAliasImpact(0, 0, 0, 0, [])

    sample_names = list(
        queryset.order_by(
            "supplier_product__supplier__name", "supplier_product__name"
        ).values_list("supplier_product__name", flat=True)[:sample_limit]
    )
    return ProductAliasImpact(
        saved_parse_matches=queryset.count(),
        active_supplier_matches=queryset.filter(
            supplier_product__is_active=True
        ).count(),
        unlocked_parse_matches=queryset.filter(locked_by_human=False).count(),
        already_linked_to_target=queryset.filter(
            supplier_product__catalog_perfume_id=perfume_id
        ).count(),
        sample_supplier_products=sample_names,
    )


def build_brand_alias_impact_preview(
    action: dict,
    *,
    sample_limit: int = 5,
) -> BrandAliasImpact:
    brand_id = action.get("brand_id")
    queryset = _brand_alias_impact_queryset(action)
    if not brand_id:
        return BrandAliasImpact(0, 0, 0, 0, [])

    sample_names = list(
        queryset.order_by(
            "supplier_product__supplier__name", "supplier_product__name"
        ).values_list("supplier_product__name", flat=True)[:sample_limit]
    )
    return BrandAliasImpact(
        saved_parse_matches=queryset.count(),
        active_supplier_matches=queryset.filter(
            supplier_product__is_active=True
        ).count(),
        unlocked_parse_matches=queryset.filter(locked_by_human=False).count(),
        already_brand_target=queryset.filter(normalized_brand_id=brand_id).count(),
        sample_supplier_products=sample_names,
    )


def _product_alias_impact_queryset(action: dict):
    brand_id = action.get("brand_id")
    perfume_id = action.get("perfume_id")
    alias_text = (action.get("alias_text") or "").strip()
    supplier_id = action.get("supplier_id") or None
    if not brand_id or not perfume_id or not alias_text:
        return ParsedSupplierProduct.objects.none()

    queryset = ParsedSupplierProduct.objects.filter(
        normalized_brand_id=brand_id,
        product_name_text__iexact=alias_text,
    ).select_related("supplier_product", "supplier_product__supplier")
    if supplier_id:
        queryset = queryset.filter(supplier_product__supplier_id=supplier_id)
    return queryset


def _brand_alias_impact_queryset(action: dict):
    brand_id = action.get("brand_id")
    alias_text = (action.get("alias_text") or "").strip()
    supplier_id = action.get("supplier_id") or None
    if not brand_id or not alias_text:
        return ParsedSupplierProduct.objects.none()

    queryset = ParsedSupplierProduct.objects.filter(
        detected_brand_text__iexact=alias_text,
    ).select_related("supplier_product", "supplier_product__supplier")
    if supplier_id:
        queryset = queryset.filter(supplier_product__supplier_id=supplier_id)
    return queryset


def apply_ai_recommendation_review(
    *,
    recommendation_id: int,
    action: str,
    user=None,
) -> AIRecommendation:
    recommendation = get_object_or_404(AIRecommendation, pk=recommendation_id)
    if action == "accept":
        recommendation.status = AIRecommendation.STATUS_ACCEPTED
    elif action == "reject":
        recommendation.status = AIRecommendation.STATUS_REJECTED
    elif action == "reset":
        recommendation.status = AIRecommendation.STATUS_PENDING
    else:
        raise ValueError("Choose accept, reject, or reset.")
    recommendation.reviewed_by = (
        user if getattr(user, "is_authenticated", False) else None
    )
    recommendation.reviewed_at = timezone.now() if action != "reset" else None
    recommendation.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
    )
    sync_learning_proposal_for_recommendation(recommendation, user=user, action=action)
    return recommendation


def _proposal_redirect_url(next_url: str, *, host: str = "") -> str:
    return safe_ai_recommendation_queue_redirect_url(next_url, host=host)


def apply_ai_learning_proposal(
    *,
    proposal_id: int,
    user=None,
    host: str = "",
    next_url: str = "",
) -> tuple[AILearningProposal, str, str]:
    proposal = get_object_or_404(
        AILearningProposal.objects.select_related(
            "source_recommendation",
            "source_recommendation__fragrantica_product",
            "source_recommendation__perfume",
            "source_recommendation__perfume__brand",
        ),
        pk=proposal_id,
    )
    recommendation = proposal.source_recommendation
    if proposal.status != AILearningProposal.STATUS_PENDING:
        raise ValueError("Only pending proposals can be applied.")
    if recommendation.status != AIRecommendation.STATUS_ACCEPTED:
        raise ValueError("Accept the AI recommendation before applying its proposal.")
    if proposal.proposal_type == AILearningProposal.PROPOSAL_PRODUCT_ALIAS:
        return _apply_product_alias_learning_proposal(proposal, user=user)
    if proposal.proposal_type == AILearningProposal.PROPOSAL_BRAND_ALIAS:
        return _apply_brand_alias_learning_proposal(proposal, user=user)
    if proposal.proposal_type != AILearningProposal.PROPOSAL_FRAGRANTICA_LINK_REVIEW:
        raise ValueError("Only supported learning proposals can be applied.")
    action = proposal.proposed_action_json or {}
    source_id = (
        action.get("fragrantica_product_id") or recommendation.fragrantica_product_id
    )
    perfume_id = action.get("perfume_id") or recommendation.perfume_id
    if not source_id or not perfume_id:
        raise ValueError("Proposal is missing a Fragrantica source or perfume target.")

    from prices.services.catalog_review import run_fragrantica_catalogue_link_action

    redirect_url = _proposal_redirect_url(next_url, host=host)
    result = run_fragrantica_catalogue_link_action(
        source_id,
        {
            "perfume_id": str(perfume_id),
            "next": redirect_url,
            "create_alias": "1",
            "apply_identity_group": "1",
            "manual_review_link": "1",
        },
        host=host,
    )
    if result.level == "error":
        return proposal, result.level, result.message

    proposal.status = AILearningProposal.STATUS_APPLIED
    proposal.reviewed_by = user if getattr(user, "is_authenticated", False) else None
    proposal.reviewed_at = timezone.now()
    impact = proposal.impact_json or {}
    impact.update(
        {
            "applied_at": proposal.reviewed_at.isoformat(),
            "applied_by_id": getattr(user, "id", None),
            "link_result": result.message,
        }
    )
    proposal.impact_json = impact
    proposal.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "impact_json",
            "updated_at",
        ]
    )
    return proposal, result.level, result.message


def apply_ready_alias_learning_proposals(
    *,
    proposal_ids: list[str] | tuple[str, ...],
    user=None,
    max_apply: int = 25,
) -> BulkAliasApplyResult:
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in proposal_ids:
        try:
            proposal_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if proposal_id in seen:
            continue
        seen.add(proposal_id)
        ordered_ids.append(proposal_id)
        if len(ordered_ids) >= max_apply:
            break

    if not ordered_ids:
        return BulkAliasApplyResult(
            requested=0,
            applied=0,
            skipped=0,
            failed=0,
            messages=["No alias proposals were selected."],
        )

    proposals = {
        proposal.pk: proposal
        for proposal in AILearningProposal.objects.select_related(
            "source_recommendation"
        ).filter(
            pk__in=ordered_ids,
            status=AILearningProposal.STATUS_PENDING,
            proposal_type__in=AI_BULK_APPLY_ALIAS_PROPOSAL_TYPES,
            source_recommendation__status=AIRecommendation.STATUS_ACCEPTED,
        )
    }
    applied = 0
    failed = 0
    messages: list[str] = []
    for proposal_id in ordered_ids:
        if proposal_id not in proposals:
            continue
        try:
            _proposal, level, message = apply_ai_learning_proposal(
                proposal_id=proposal_id,
                user=user,
            )
        except ValueError as exc:
            failed += 1
            messages.append(str(exc))
            continue
        if level == "success":
            applied += 1
        else:
            failed += 1
        messages.append(message)

    skipped = len(ordered_ids) - len(proposals)
    return BulkAliasApplyResult(
        requested=len(ordered_ids),
        applied=applied,
        skipped=skipped,
        failed=failed,
        messages=messages,
    )


def refresh_product_alias_learning_proposal_parses(
    *,
    proposal_id: int,
    max_refresh: int = 1000,
    parse_saver=None,
) -> tuple[AILearningProposal, str, str]:
    proposal = get_object_or_404(AILearningProposal, pk=proposal_id)
    if proposal.proposal_type != AILearningProposal.PROPOSAL_PRODUCT_ALIAS:
        raise ValueError("Only product-alias proposals can refresh affected parses.")
    if proposal.status != AILearningProposal.STATUS_APPLIED:
        raise ValueError("Apply the product-alias proposal before refreshing parses.")
    action = proposal.proposed_action_json or {}
    if parse_saver is None:
        from assistant_linking.services.normalizer import save_parse

        parse_saver = save_parse

    queryset = _product_alias_impact_queryset(action)
    matched_before_refresh = queryset.count()
    skipped_locked = queryset.filter(locked_by_human=True).count()
    parse_ids = list(
        queryset.filter(locked_by_human=False)
        .order_by("supplier_product_id")
        .values_list("supplier_product_id", flat=True)[:max_refresh]
    )
    refreshed = 0
    for supplier_product_id in parse_ids:
        product = (
            ParsedSupplierProduct.objects.select_related("supplier_product")
            .get(supplier_product_id=supplier_product_id)
            .supplier_product
        )
        parse_saver(product)
        refreshed += 1

    result = ProductAliasRefreshResult(
        refreshed=refreshed,
        skipped_locked=skipped_locked,
        matched_before_refresh=matched_before_refresh,
    )
    impact = proposal.impact_json or {}
    impact.update(
        {
            "last_refresh": {
                "refreshed": result.refreshed,
                "skipped_locked": result.skipped_locked,
                "matched_before_refresh": result.matched_before_refresh,
                "max_refresh": max_refresh,
                "refreshed_at": timezone.now().isoformat(),
            }
        }
    )
    proposal.impact_json = impact
    proposal.save(update_fields=["impact_json", "updated_at"])
    return proposal, "success", result.message


def refresh_brand_alias_learning_proposal_parses(
    *,
    proposal_id: int,
    max_refresh: int = 1000,
    parse_saver=None,
) -> tuple[AILearningProposal, str, str]:
    proposal = get_object_or_404(AILearningProposal, pk=proposal_id)
    if proposal.proposal_type != AILearningProposal.PROPOSAL_BRAND_ALIAS:
        raise ValueError("Only brand-alias proposals can refresh affected parses.")
    if proposal.status != AILearningProposal.STATUS_APPLIED:
        raise ValueError("Apply the brand-alias proposal before refreshing parses.")
    action = proposal.proposed_action_json or {}
    if parse_saver is None:
        from assistant_linking.services.normalizer import save_parse

        parse_saver = save_parse

    queryset = _brand_alias_impact_queryset(action)
    matched_before_refresh = queryset.count()
    skipped_locked = queryset.filter(locked_by_human=True).count()
    parse_ids = list(
        queryset.filter(locked_by_human=False)
        .order_by("supplier_product_id")
        .values_list("supplier_product_id", flat=True)[:max_refresh]
    )
    refreshed = 0
    for supplier_product_id in parse_ids:
        product = (
            ParsedSupplierProduct.objects.select_related("supplier_product")
            .get(supplier_product_id=supplier_product_id)
            .supplier_product
        )
        parse_saver(product)
        refreshed += 1

    result = BrandAliasRefreshResult(
        refreshed=refreshed,
        skipped_locked=skipped_locked,
        matched_before_refresh=matched_before_refresh,
    )
    impact = proposal.impact_json or {}
    impact.update(
        {
            "last_refresh": {
                "refreshed": result.refreshed,
                "skipped_locked": result.skipped_locked,
                "matched_before_refresh": result.matched_before_refresh,
                "max_refresh": max_refresh,
                "refreshed_at": timezone.now().isoformat(),
            }
        }
    )
    proposal.impact_json = impact
    proposal.save(update_fields=["impact_json", "updated_at"])
    return proposal, "success", result.message


def refresh_ai_learning_proposal_parses(
    *,
    proposal_id: int,
    max_refresh: int = 1000,
    parse_saver=None,
) -> tuple[AILearningProposal, str, str]:
    proposal = get_object_or_404(AILearningProposal, pk=proposal_id)
    if proposal.proposal_type == AILearningProposal.PROPOSAL_PRODUCT_ALIAS:
        return refresh_product_alias_learning_proposal_parses(
            proposal_id=proposal_id,
            max_refresh=max_refresh,
            parse_saver=parse_saver,
        )
    if proposal.proposal_type == AILearningProposal.PROPOSAL_BRAND_ALIAS:
        return refresh_brand_alias_learning_proposal_parses(
            proposal_id=proposal_id,
            max_refresh=max_refresh,
            parse_saver=parse_saver,
        )
    raise ValueError("Only alias proposals can refresh affected parses.")


def regenerate_alias_learning_proposal_preview(
    *,
    proposal_id: int,
    user=None,
) -> tuple[AILearningProposal, str, str]:
    proposal = get_object_or_404(AILearningProposal, pk=proposal_id)
    if proposal.status != AILearningProposal.STATUS_PENDING:
        raise ValueError("Only pending alias proposals can regenerate previews.")
    if proposal.proposal_type == AILearningProposal.PROPOSAL_PRODUCT_ALIAS:
        preview = build_product_alias_impact_preview(
            proposal.proposed_action_json or {}
        ).as_json()
        label = "product alias"
    elif proposal.proposal_type == AILearningProposal.PROPOSAL_BRAND_ALIAS:
        preview = build_brand_alias_impact_preview(
            proposal.proposed_action_json or {}
        ).as_json()
        label = "brand alias"
    else:
        raise ValueError("Only alias proposals can regenerate impact previews.")

    impact = proposal.impact_json or {}
    impact["preview"] = preview
    impact["preview_refreshed_at"] = timezone.now().isoformat()
    impact["preview_refreshed_by_id"] = getattr(user, "id", None)
    proposal.impact_json = impact
    proposal.save(update_fields=["impact_json", "updated_at"])
    return proposal, "success", f"Regenerated {label} impact preview."


def revert_ai_learning_proposal_alias(
    *,
    proposal_id: int,
    user=None,
) -> tuple[AILearningProposal, str, str]:
    proposal = get_object_or_404(AILearningProposal, pk=proposal_id)
    if proposal.status != AILearningProposal.STATUS_APPLIED:
        raise ValueError("Only applied alias proposals can be reverted.")
    impact = proposal.impact_json or {}
    if proposal.proposal_type == AILearningProposal.PROPOSAL_PRODUCT_ALIAS:
        alias_id = impact.get("product_alias_id")
        alias_model = ProductAlias
        label = "product alias"
    elif proposal.proposal_type == AILearningProposal.PROPOSAL_BRAND_ALIAS:
        alias_id = impact.get("brand_alias_id")
        alias_model = BrandAlias
        label = "brand alias"
    else:
        raise ValueError("Only alias proposals can be reverted.")
    if not alias_id:
        raise ValueError("Proposal does not record the alias it created.")

    with transaction.atomic():
        try:
            alias = alias_model.objects.select_for_update().get(pk=alias_id)
        except alias_model.DoesNotExist:
            raise ValueError("The alias created by this proposal no longer exists.")
        if alias.active:
            alias.active = False
            alias.save(update_fields=["active", "updated_at"])
            result_message = f"Deactivated {label} #{alias.id}."
        else:
            result_message = f"{label.title()} #{alias.id} was already inactive."

        proposal.status = AILearningProposal.STATUS_REVERTED
        proposal.reviewed_by = (
            user if getattr(user, "is_authenticated", False) else None
        )
        proposal.reviewed_at = timezone.now()
        impact.update(
            {
                "reverted_at": proposal.reviewed_at.isoformat(),
                "reverted_by_id": getattr(user, "id", None),
                "revert_result": result_message,
            }
        )
        proposal.impact_json = impact
        proposal.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "impact_json",
                "updated_at",
            ]
        )
    return proposal, "success", result_message


def _apply_product_alias_learning_proposal(
    proposal: AILearningProposal,
    *,
    user=None,
) -> tuple[AILearningProposal, str, str]:
    action = proposal.proposed_action_json or {}
    brand_id = action.get("brand_id")
    perfume_id = action.get("perfume_id")
    alias_text = (action.get("alias_text") or "").strip()
    canonical_text = (action.get("canonical_text") or "").strip()
    supplier_id = action.get("supplier_id") or None
    if not brand_id or not perfume_id or not alias_text or not canonical_text:
        raise ValueError("Proposal is missing product-alias target data.")
    _ensure_alias_proposal_preview_current(proposal)

    with transaction.atomic():
        existing = ProductAlias.objects.select_for_update().filter(
            active=True,
            brand_id=brand_id,
            perfume_id=perfume_id,
            alias_text__iexact=alias_text,
        )
        if supplier_id:
            existing = existing.filter(
                Q(supplier_id=supplier_id) | Q(supplier__isnull=True)
            )
        else:
            existing = existing.filter(supplier__isnull=True)
        if existing.exists():
            return proposal, "error", "A matching active product alias already exists."
        alias = ProductAlias.objects.create(
            brand_id=brand_id,
            perfume_id=perfume_id,
            alias_text=alias_text,
            canonical_text=canonical_text,
            supplier_id=supplier_id,
            concentration=action.get("concentration") or "",
            audience=action.get("audience") or "",
            collection_name=action.get("collection_name") or "",
            priority=90,
        )
        proposal.status = AILearningProposal.STATUS_APPLIED
        proposal.reviewed_by = (
            user if getattr(user, "is_authenticated", False) else None
        )
        proposal.reviewed_at = timezone.now()
        impact = proposal.impact_json or {}
        impact.update(
            {
                "applied_at": proposal.reviewed_at.isoformat(),
                "applied_by_id": getattr(user, "id", None),
                "product_alias_id": alias.id,
                "link_result": f"Created ProductAlias #{alias.id}.",
            }
        )
        proposal.impact_json = impact
        proposal.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "impact_json",
                "updated_at",
            ]
        )
    return proposal, "success", f"Created product alias '{alias_text}'."


def _apply_brand_alias_learning_proposal(
    proposal: AILearningProposal,
    *,
    user=None,
) -> tuple[AILearningProposal, str, str]:
    action = proposal.proposed_action_json or {}
    brand_id = action.get("brand_id")
    alias_text = (action.get("alias_text") or "").strip()
    canonical_text = (action.get("canonical_text") or "").strip()
    supplier_id = action.get("supplier_id") or None
    if not brand_id or not alias_text or not canonical_text:
        raise ValueError("Proposal is missing brand-alias target data.")
    if not _brand_alias_text_is_safe(alias_text, target_brand_id=brand_id):
        raise ValueError("Brand alias text is too generic or conflicts with a brand.")
    _ensure_alias_proposal_preview_current(proposal)

    with transaction.atomic():
        existing = BrandAlias.objects.select_for_update().filter(
            active=True,
            brand_id=brand_id,
            alias_text__iexact=alias_text,
        )
        if supplier_id:
            existing = existing.filter(
                Q(supplier_id=supplier_id) | Q(supplier__isnull=True)
            )
        else:
            existing = existing.filter(supplier__isnull=True)
        if existing.exists():
            return proposal, "error", "A matching active brand alias already exists."
        alias = BrandAlias.objects.create(
            brand_id=brand_id,
            alias_text=alias_text,
            normalized_alias=normalize_alias_value(alias_text),
            supplier_id=supplier_id,
            priority=90,
        )
        proposal.status = AILearningProposal.STATUS_APPLIED
        proposal.reviewed_by = (
            user if getattr(user, "is_authenticated", False) else None
        )
        proposal.reviewed_at = timezone.now()
        impact = proposal.impact_json or {}
        impact.update(
            {
                "applied_at": proposal.reviewed_at.isoformat(),
                "applied_by_id": getattr(user, "id", None),
                "brand_alias_id": alias.id,
                "link_result": f"Created BrandAlias #{alias.id}.",
            }
        )
        proposal.impact_json = impact
        proposal.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "impact_json",
                "updated_at",
            ]
        )
    return proposal, "success", f"Created brand alias '{alias_text}'."


def _proposal_summary(recommendation: AIRecommendation) -> str:
    if recommendation.fragrantica_product_id and recommendation.perfume_id:
        return (
            "Review the AI-ranked Fragrantica candidate in the linking workbench. "
            "Creating this proposal does not link products or change catalogue data."
        )
    return "Review this accepted AI recommendation before creating live knowledge."


def _kb_product_alias_proposal_summary(recommendation: AIRecommendation) -> str:
    payload = recommendation.recommendation_json or {}
    count = payload.get("decision_count") or 0
    return (
        f"Create a reviewed product alias from {count} repeated manual link "
        "decision(s). Applying this proposal creates alias knowledge only; "
        "affected supplier rows still need explicit parse refresh if saved parses "
        "should be updated."
    )


def _kb_brand_alias_proposal_summary(recommendation: AIRecommendation) -> str:
    payload = recommendation.recommendation_json or {}
    count = payload.get("decision_count") or 0
    return (
        f"Create a reviewed brand alias from {count} repeated manual link "
        "decision(s). Applying this proposal creates alias knowledge only; "
        "affected supplier rows still need explicit parse refresh if saved parses "
        "should be updated."
    )


def sync_learning_proposal_for_recommendation(
    recommendation: AIRecommendation,
    *,
    user=None,
    action: str,
) -> AILearningProposal | None:
    if action == "accept" and (
        recommendation.task_type == AIRecommendation.TASK_FRAGRANTICA_LINK_RERANK
    ):
        if not recommendation.perfume_id or not recommendation.fragrantica_product_id:
            return None
        proposal, _created = AILearningProposal.objects.update_or_create(
            source_recommendation=recommendation,
            defaults={
                "proposal_type": AILearningProposal.PROPOSAL_FRAGRANTICA_LINK_REVIEW,
                "status": AILearningProposal.STATUS_PENDING,
                "title": f"Review Fragrantica link for {recommendation.perfume}",
                "summary": _proposal_summary(recommendation),
                "proposed_action_json": {
                    "action": "review_fragrantica_link",
                    "perfume_id": recommendation.perfume_id,
                    "fragrantica_product_id": recommendation.fragrantica_product_id,
                    "workbench_url": ai_recommendation_workbench_url(recommendation),
                },
                "evidence_json": {
                    "ai_recommendation_id": recommendation.pk,
                    "confidence": recommendation.confidence,
                    "risk_level": recommendation.risk_level,
                    "reasoning": recommendation.reasoning,
                    "candidate_notes": (
                        recommendation.recommendation_json.get("candidate_notes", [])
                        if isinstance(recommendation.recommendation_json, dict)
                        else []
                    ),
                },
                "impact_json": {
                    "mutates_on_create": False,
                    "requires_staff_link_action": True,
                    "local_concentration_stays_local": True,
                },
                "reviewed_by": None,
                "reviewed_at": None,
            },
        )
        return proposal

    if action == "accept" and (
        recommendation.task_type == AIRecommendation.TASK_KB_SUGGESTION
    ):
        payload = recommendation.recommendation_json or {}
        proposal_type = payload.get("proposal_type")
        if proposal_type == AILearningProposal.PROPOSAL_BRAND_ALIAS:
            proposed_action = {
                "action": "create_brand_alias",
                "alias_text": payload.get("alias_text"),
                "canonical_text": payload.get("canonical_text"),
                "brand_id": payload.get("brand_id"),
                "supplier_id": payload.get("supplier_id"),
            }
            impact_preview = build_brand_alias_impact_preview(proposed_action)
            proposal, _created = AILearningProposal.objects.update_or_create(
                source_recommendation=recommendation,
                defaults={
                    "proposal_type": AILearningProposal.PROPOSAL_BRAND_ALIAS,
                    "status": AILearningProposal.STATUS_PENDING,
                    "title": (
                        "Create brand alias "
                        f"{payload.get('alias_text')} -> {payload.get('canonical_text')}"
                    ),
                    "summary": _kb_brand_alias_proposal_summary(recommendation),
                    "proposed_action_json": proposed_action,
                    "evidence_json": {
                        "ai_recommendation_id": recommendation.pk,
                        "confidence": recommendation.confidence,
                        "risk_level": recommendation.risk_level,
                        "reasoning": recommendation.reasoning,
                        "decision_ids": payload.get("decision_ids") or [],
                        "decision_count": payload.get("decision_count") or 0,
                        "supplier_names": payload.get("supplier_names") or [],
                    },
                    "impact_json": {
                        "mutates_on_create": False,
                        "requires_staff_apply_action": True,
                        "creates_brand_alias": True,
                        "requires_explicit_reparse": True,
                        "preview": impact_preview.as_json(),
                    },
                    "reviewed_by": None,
                    "reviewed_at": None,
                },
            )
            return proposal
        if proposal_type != AILearningProposal.PROPOSAL_PRODUCT_ALIAS:
            return None
        proposed_action = {
            "action": "create_product_alias",
            "alias_text": payload.get("alias_text"),
            "canonical_text": payload.get("canonical_text"),
            "brand_id": payload.get("brand_id"),
            "perfume_id": payload.get("perfume_id"),
            "supplier_id": payload.get("supplier_id"),
            "collection_name": payload.get("collection_name") or "",
            "concentration": payload.get("concentration") or "",
            "audience": payload.get("audience") or "",
        }
        impact_preview = build_product_alias_impact_preview(proposed_action)
        proposal, _created = AILearningProposal.objects.update_or_create(
            source_recommendation=recommendation,
            defaults={
                "proposal_type": AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
                "status": AILearningProposal.STATUS_PENDING,
                "title": (
                    "Create product alias "
                    f"{payload.get('alias_text')} -> {payload.get('canonical_text')}"
                ),
                "summary": _kb_product_alias_proposal_summary(recommendation),
                "proposed_action_json": proposed_action,
                "evidence_json": {
                    "ai_recommendation_id": recommendation.pk,
                    "confidence": recommendation.confidence,
                    "risk_level": recommendation.risk_level,
                    "reasoning": recommendation.reasoning,
                    "decision_ids": payload.get("decision_ids") or [],
                    "decision_count": payload.get("decision_count") or 0,
                    "supplier_names": payload.get("supplier_names") or [],
                },
                "impact_json": {
                    "mutates_on_create": False,
                    "requires_staff_apply_action": True,
                    "creates_product_alias": True,
                    "requires_explicit_reparse": True,
                    "preview": impact_preview.as_json(),
                },
                "reviewed_by": None,
                "reviewed_at": None,
            },
        )
        return proposal

    proposal = learning_proposal_for_recommendation(recommendation)
    if proposal and proposal.status == AILearningProposal.STATUS_PENDING:
        proposal.status = AILearningProposal.STATUS_REJECTED
        proposal.reviewed_by = (
            user if getattr(user, "is_authenticated", False) else None
        )
        proposal.reviewed_at = timezone.now()
        proposal.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
        )
    return proposal
