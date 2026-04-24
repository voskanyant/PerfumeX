from __future__ import annotations

from django.conf import settings

from assistant_core.models import BrandWatchProfile, DetectedChange, ResearchJob
from assistant_core.services.mock_brand_research import run_mock_brand_watch
from assistant_core.services.openai_responses import OpenAIUnavailable, create_structured_response, use_openai


RESEARCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["brand_id", "products_found", "changes", "warnings"],
    "properties": {
        "brand_id": {"type": "integer"},
        "products_found": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "changes": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def run_openai_brand_watch(profile_id: int) -> ResearchJob:
    if not use_openai():
        return run_mock_brand_watch(profile_id)
    profile = BrandWatchProfile.objects.select_related("brand").get(pk=profile_id)
    try:
        payload = create_structured_response(
            model=getattr(settings, "OPENAI_MODEL_RESEARCH", "gpt-5.4"),
            instructions="Research only from supplied context. Return reviewable changes; do not publish.",
            input_text=str({"brand": profile.brand.name, "official_url": profile.official_url, "trusted_sources": profile.trusted_sources_json}),
            schema_name="brand_research_result",
            schema=RESEARCH_SCHEMA,
        )
    except (OpenAIUnavailable, ValueError, TypeError):
        return run_mock_brand_watch(profile_id)
    job = ResearchJob.objects.create(
        job_type="brand_watch_openai",
        status=ResearchJob.STATUS_FINISHED,
        brand=profile.brand,
        query=profile.brand.name,
        raw_result_json=payload,
        result_summary=f"OpenAI returned {len(payload.get('changes', []))} reviewable changes.",
    )
    for change in payload.get("changes", []):
        DetectedChange.objects.create(
            brand_profile=profile,
            change_type=change.get("change_type", "changed_field"),
            field_name=change.get("field_name") or "",
            old_value_json=change.get("old_value") or {},
            new_value_json=change.get("new_value") or {},
            explanation=change.get("explanation", ""),
            confidence=change.get("confidence", 50),
            source_urls_json=change.get("source_urls", []),
        )
    return job
