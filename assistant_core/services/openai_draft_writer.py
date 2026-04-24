from __future__ import annotations

from django.conf import settings

from assistant_core.services.mock_description_generator import create_mock_draft
from assistant_core.services.openai_responses import OpenAIUnavailable, create_structured_response, use_openai
from catalog.models import AIDraft, FactClaim, Perfume


DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["short_description", "long_description", "beginner_description", "seo_title", "seo_description", "mood_tags", "warnings"],
    "properties": {
        "short_description": {"type": "string"},
        "long_description": {"type": "string"},
        "beginner_description": {"type": "string"},
        "seo_title": {"type": "string"},
        "seo_description": {"type": "string"},
        "mood_tags": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def create_openai_draft(perfume_id: int, draft_type: str = "description") -> AIDraft:
    if not use_openai():
        return create_mock_draft(perfume_id, draft_type=draft_type)
    perfume = Perfume.objects.select_related("brand").get(pk=perfume_id)
    claims = list(FactClaim.objects.filter(perfume=perfume, status=FactClaim.STATUS_APPROVED))
    try:
        payload = create_structured_response(
            model=getattr(settings, "OPENAI_MODEL_WRITER", "gpt-5.4-mini"),
            instructions="Write draft copy from approved claims only. Do not add unsupported facts.",
            input_text=str({"perfume": str(perfume), "claims": [{"field": c.field_name, "value": c.value_json} for c in claims]}),
            schema_name="description_draft",
            schema=DRAFT_SCHEMA,
        )
    except (OpenAIUnavailable, ValueError, TypeError):
        return create_mock_draft(perfume_id, draft_type=draft_type)
    return AIDraft.objects.create(
        perfume=perfume,
        draft_type=draft_type,
        source_claims_json=[claim.id for claim in claims],
        content_json=payload,
        model_name=getattr(settings, "OPENAI_MODEL_WRITER", "gpt-5.4-mini"),
        prompt_version="openai-v1",
        status=AIDraft.STATUS_PENDING,
        warnings=payload.get("warnings", []),
    )
