from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from django.conf import settings

OpenAI = None
logger = logging.getLogger(__name__)


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
    return OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        timeout=30.0,
        max_retries=2,
    )


def _retryable_openai_errors():
    try:
        from openai import APIConnectionError, APITimeoutError, RateLimitError
    except ImportError:
        return ()
    return (APITimeoutError, RateLimitError, APIConnectionError)


def call_openai(model: str, **kwargs):
    client = get_client()
    start = time.monotonic()
    try:
        response = client.responses.create(model=model, **kwargs)
    except _retryable_openai_errors() as exc:
        logger.warning("openai retryable error: %s", exc)
        raise
    except Exception:
        logger.exception("openai call failed")
        raise

    usage = getattr(response, "usage", None)
    logger.info(
        "openai_call model=%s duration_ms=%d input_tokens=%s output_tokens=%s",
        model,
        int((time.monotonic() - start) * 1000),
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
    )
    return response


def create_structured_response(*, model: str, instructions: str, input_text: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    response = call_openai(
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
