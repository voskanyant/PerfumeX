from __future__ import annotations

import hashlib
import json
from typing import Any

from django.conf import settings

from assistant_core.services.openai_responses import (
    create_structured_response,
    use_openai,
)
from assistant_linking.models import AIRecommendation, FragranticaProduct


AI_ADVISOR_PROMPT_VERSION = "ai-advisor-v1"

FRAGRANTICA_RERANK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "recommended_candidate_id",
        "confidence",
        "risk_level",
        "reasoning",
        "candidate_notes",
    ],
    "properties": {
        "recommended_candidate_id": {"type": ["integer", "null"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high", "unknown"]},
        "reasoning": {"type": "string"},
        "candidate_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_id", "note"],
                "properties": {
                    "candidate_id": {"type": "integer"},
                    "note": {"type": "string"},
                },
            },
        },
    },
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_input_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _value_from(source: Any, name: str, default: Any = "") -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _collection_name(source: Any) -> str:
    collection = _value_from(source, "collection", None)
    if collection:
        return str(collection)
    return str(_value_from(source, "collection_name", "") or "")


def _candidate_source(candidate: Any) -> Any:
    return _value_from(candidate, "source", candidate)


def build_fragrantica_rerank_context(
    *, perfume: Any, candidates: list[Any]
) -> dict[str, Any]:
    brand = _value_from(perfume, "brand", None)
    candidate_rows = []
    for candidate in candidates:
        source = _candidate_source(candidate)
        candidate_rows.append(
            {
                "id": int(_value_from(source, "id")),
                "brand_name": str(_value_from(source, "brand_name", "") or ""),
                "name": str(_value_from(source, "name", "") or ""),
                "collection_name": _collection_name(source),
                "audience": str(_value_from(source, "audience", "") or ""),
                "release_year": _value_from(source, "release_year", None),
                "existing_match_perfume_id": _value_from(
                    source, "matched_perfume_id", None
                ),
                "deterministic_score": _value_from(candidate, "score", None),
                "deterministic_reason": str(
                    _value_from(candidate, "reason", "")
                    or _value_from(candidate, "match_reason", "")
                    or ""
                ),
            }
        )
    return {
        "task": AIRecommendation.TASK_FRAGRANTICA_LINK_RERANK,
        "policy": {
            "review_only": True,
            "do_not_invent_ids": True,
            "do_not_mutate_links": True,
            "local_concentration_stays_local": True,
        },
        "local_perfume": {
            "id": _value_from(perfume, "id", None),
            "brand_name": str(brand or _value_from(perfume, "brand_name", "") or ""),
            "name": str(_value_from(perfume, "name", "") or ""),
            "concentration": str(_value_from(perfume, "concentration", "") or ""),
            "collection_name": _collection_name(perfume),
            "audience": str(_value_from(perfume, "audience", "") or ""),
            "release_year": _value_from(perfume, "release_year", None),
        },
        "candidates": candidate_rows,
    }


def validate_fragrantica_rerank_payload(
    payload: dict[str, Any], *, candidate_ids: set[int]
) -> dict[str, Any]:
    recommended_id = payload.get("recommended_candidate_id")
    if recommended_id is not None:
        try:
            recommended_id = int(recommended_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "recommended_candidate_id must be an integer or null."
            ) from exc
        if recommended_id not in candidate_ids:
            raise ValueError("AI recommended an unknown Fragrantica candidate ID.")

    try:
        confidence = int(payload.get("confidence", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be an integer from 0 to 100.") from exc
    if not 0 <= confidence <= 100:
        raise ValueError("confidence must be an integer from 0 to 100.")

    risk_level = str(payload.get("risk_level") or AIRecommendation.RISK_UNKNOWN)
    valid_risk_levels = {choice[0] for choice in AIRecommendation.RISK_CHOICES}
    if risk_level not in valid_risk_levels:
        raise ValueError("risk_level must be low, medium, high, or unknown.")

    notes = []
    for item in payload.get("candidate_notes", []) or []:
        try:
            candidate_id = int(item.get("candidate_id"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(
                "candidate_notes must reference integer candidate IDs."
            ) from exc
        if candidate_id not in candidate_ids:
            raise ValueError("candidate_notes referenced an unknown candidate ID.")
        notes.append({"candidate_id": candidate_id, "note": str(item.get("note", ""))})

    return {
        "recommended_candidate_id": recommended_id,
        "confidence": confidence,
        "risk_level": risk_level,
        "reasoning": str(payload.get("reasoning", "") or ""),
        "candidate_notes": notes,
    }


def create_fragrantica_rerank_recommendation(
    *,
    perfume: Any,
    candidates: list[Any],
    supplier_product: Any | None = None,
    parsed_product: Any | None = None,
    payload: dict[str, Any] | None = None,
    call_model: bool = False,
) -> AIRecommendation:
    context = build_fragrantica_rerank_context(perfume=perfume, candidates=candidates)
    candidate_ids = {row["id"] for row in context["candidates"]}
    model_name = getattr(settings, "OPENAI_MODEL_SUGGESTION", "gpt-5.4-mini")

    if payload is None and call_model and use_openai():
        payload = create_structured_response(
            model=model_name,
            instructions=(
                "Review only. Rerank existing Fragrantica candidates for the local "
                "perfume. Do not invent IDs, links, aliases, or catalogue facts."
            ),
            input_text=canonical_json(context),
            schema_name="fragrantica_link_rerank",
            schema=FRAGRANTICA_RERANK_SCHEMA,
        )

    if payload is None:
        model_name = "not-called"
        payload = {
            "recommended_candidate_id": None,
            "confidence": 0,
            "risk_level": AIRecommendation.RISK_UNKNOWN,
            "reasoning": "AI model was not called; stored bounded context for later review.",
            "candidate_notes": [],
        }

    recommendation = validate_fragrantica_rerank_payload(
        payload,
        candidate_ids=candidate_ids,
    )
    fragrantica_product = None
    if recommendation["recommended_candidate_id"] is not None:
        fragrantica_product = FragranticaProduct.objects.filter(
            pk=recommendation["recommended_candidate_id"]
        ).first()

    return AIRecommendation.objects.create(
        task_type=AIRecommendation.TASK_FRAGRANTICA_LINK_RERANK,
        supplier_product=supplier_product,
        parsed_product=parsed_product,
        fragrantica_product=fragrantica_product,
        perfume=perfume,
        input_hash=stable_input_hash(context),
        prompt_version=AI_ADVISOR_PROMPT_VERSION,
        model_name=model_name,
        confidence=recommendation["confidence"],
        risk_level=recommendation["risk_level"],
        input_context_json=context,
        recommendation_json=recommendation,
        reasoning=recommendation["reasoning"],
    )


def latest_fragrantica_rerank_recommendation(
    *,
    perfume: Any,
    candidates: list[Any],
    recommendation_model=AIRecommendation,
) -> AIRecommendation | None:
    context = build_fragrantica_rerank_context(perfume=perfume, candidates=candidates)
    return (
        recommendation_model.objects.filter(
            task_type=AIRecommendation.TASK_FRAGRANTICA_LINK_RERANK,
            input_hash=stable_input_hash(context),
        )
        .order_by("-created_at", "-id")
        .first()
    )
