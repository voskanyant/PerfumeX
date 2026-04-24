from __future__ import annotations

from django.conf import settings

from assistant_core.services.context_builder import build_assistant_context
from assistant_core.services.openai_responses import OpenAIUnavailable, create_structured_response, use_openai
from assistant_linking.models import LinkSuggestion
from assistant_linking.services.mock_suggester import generate_link_suggestions as generate_mock_suggestions
from prices.models import SupplierProduct


SUGGESTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["supplier_product_id", "suggestions", "needs_human_review", "global_warnings"],
    "properties": {
        "supplier_product_id": {"type": "integer"},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["suggested_perfume_id", "suggested_variant_id", "confidence", "reasoning", "rules_used", "uncertainties"],
                "properties": {
                    "suggested_perfume_id": {"type": ["integer", "null"]},
                    "suggested_variant_id": {"type": ["integer", "null"]},
                    "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reasoning": {"type": "array", "items": {"type": "string"}},
                    "rules_used": {"type": "array", "items": {"type": "string"}},
                    "uncertainties": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "needs_human_review": {"type": "boolean"},
        "global_warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def generate_openai_link_suggestions(supplier_product_id: int, *, limit: int = 5) -> list[dict]:
    if not use_openai():
        return generate_mock_suggestions(supplier_product_id, limit=limit)
    product = SupplierProduct.objects.get(pk=supplier_product_id)
    context = build_assistant_context(supplier_product_id=supplier_product_id)
    try:
        payload = create_structured_response(
            model=getattr(settings, "OPENAI_MODEL_SUGGESTION", "gpt-5.4-mini"),
            instructions="Return catalogue link suggestions only. Do not invent IDs. Human review is required.",
            input_text=str(context),
            schema_name="link_suggestion_result",
            schema=SUGGESTION_SCHEMA,
        )
    except (OpenAIUnavailable, ValueError, TypeError):
        return generate_mock_suggestions(supplier_product_id, limit=limit)
    created = []
    for suggestion in payload.get("suggestions", [])[:limit]:
        obj = LinkSuggestion.objects.create(
            supplier_product=product,
            suggested_perfume_id=suggestion.get("suggested_perfume_id"),
            suggested_variant_id=suggestion.get("suggested_variant_id"),
            confidence=suggestion.get("confidence", 0),
            reasoning="\n".join(suggestion.get("reasoning", [])),
            rules_used_json=suggestion.get("rules_used", []),
            uncertainties_json=suggestion.get("uncertainties", []),
            source_engine="openai",
        )
        created.append({"id": obj.id, **suggestion})
    return created
