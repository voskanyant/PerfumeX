from __future__ import annotations

import json
import os
from typing import Any

from django.conf import settings

OpenAI = None


class OpenAIUnavailable(RuntimeError):
    pass


def use_openai() -> bool:
    return bool(getattr(settings, "ASSISTANT_USE_OPENAI", False) and os.getenv("OPENAI_API_KEY"))


def get_client():
    if not use_openai():
        raise OpenAIUnavailable("OpenAI is disabled or OPENAI_API_KEY is missing.")
    global OpenAI
    try:
        if OpenAI is None:
            from openai import OpenAI as ImportedOpenAI

            OpenAI = ImportedOpenAI
    except ImportError as exc:
        raise OpenAIUnavailable("The openai package is not installed.") from exc
    return OpenAI()


def create_structured_response(*, model: str, instructions: str, input_text: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    client = get_client()
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=input_text,
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": True,
            }
        },
    )
    output_text = getattr(response, "output_text", "") or ""
    if not output_text:
        raise OpenAIUnavailable("OpenAI response did not include output_text.")
    return json.loads(output_text)
