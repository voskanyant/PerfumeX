from __future__ import annotations

from django.db.models import Q

from assistant_core import models
from catalog.models import AIDraft, FactClaim, Perfume


def build_dashboard_context() -> dict:
    from assistant_linking.models import (
        BrandAlias,
        ConcentrationAlias,
        LinkSuggestion,
        ManualLinkDecision,
        ParsedSupplierProduct,
        ProductAlias,
    )
    from assistant_linking.services.garbage import GARBAGE_MODIFIER

    knowledge_count = (
        models.GlobalRule.objects.count()
        + models.SupplierRule.objects.count()
        + models.KnowledgeNote.objects.count()
        + BrandAlias.objects.count()
        + ProductAlias.objects.count()
        + ConcentrationAlias.objects.count()
    )
    parsed_queryset = ParsedSupplierProduct.objects.exclude(
        modifiers__contains=[GARBAGE_MODIFIER]
    )
    complete_parse_query = (
        Q(normalized_brand__isnull=False)
        & ~Q(product_name_text="")
        & ~Q(concentration="")
        & Q(size_ml__isnull=False)
    )
    normalization_issue_count = parsed_queryset.exclude(complete_parse_query).count()

    return {
        "cards": [
            (
                "Normalisation issues",
                "assistant_linking:normalization_dashboard",
                normalization_issue_count,
            ),
            ("Catalogue", "prices:our_product_list", Perfume.objects.count()),
            (
                "Linking Workbench",
                "assistant_linking:group_queue",
                LinkSuggestion.objects.filter(
                    status=LinkSuggestion.STATUS_PENDING
                ).count(),
            ),
            ("Knowledge Base", "assistant_core:knowledge", knowledge_count),
            (
                "Brand Managers",
                "assistant_core:brand_manager_list",
                models.BrandWatchProfile.objects.filter(active=True).count(),
            ),
            (
                "Research Review",
                "assistant_core:research_jobs",
                models.DetectedChange.objects.filter(
                    status=models.DetectedChange.STATUS_PENDING
                ).count(),
            ),
            (
                "AI Drafts",
                "assistant_core:drafts",
                AIDraft.objects.filter(status=AIDraft.STATUS_PENDING).count(),
            ),
        ],
        "pending_approvals": models.DetectedChange.objects.filter(
            status=models.DetectedChange.STATUS_PENDING
        ).count()
        + FactClaim.objects.filter(status=FactClaim.STATUS_PENDING).count(),
        "low_confidence": parsed_queryset.filter(confidence__lt=75).count(),
        "normalization_issue_count": normalization_issue_count,
        "recent_decisions": ManualLinkDecision.objects.select_related(
            "supplier_product"
        ).order_by("-created_at")[:8],
    }
