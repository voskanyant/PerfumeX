from __future__ import annotations

from catalog.models import AIDraft, FactClaim, Perfume


def create_mock_draft(perfume_id: int, draft_type: str = "description") -> AIDraft:
    perfume = Perfume.objects.select_related("brand").get(pk=perfume_id)
    claims = list(FactClaim.objects.filter(perfume=perfume, status=FactClaim.STATUS_APPROVED).order_by("field_name", "created_at"))
    fact_text = "; ".join([f"{claim.field_name}: {claim.value_json}" for claim in claims]) or "No approved facts yet."
    content = {
        "short_description": f"{perfume.name} by {perfume.brand.name}.",
        "long_description": f"Draft based only on approved claims. {fact_text}",
        "beginner_description": f"A review draft for {perfume.name} using approved PerfumeX facts.",
        "seo_title": f"{perfume.brand.name} {perfume.name} review",
        "seo_description": f"Source-backed draft for {perfume.brand.name} {perfume.name}.",
        "mood_tags": [],
        "warnings": [] if claims else ["No approved claims were available."],
    }
    return AIDraft.objects.create(
        perfume=perfume,
        draft_type=draft_type,
        source_claims_json=[claim.id for claim in claims],
        content_json=content,
        model_name="mock",
        prompt_version="mock-v1",
        status=AIDraft.STATUS_PENDING,
        warnings=content["warnings"],
    )
