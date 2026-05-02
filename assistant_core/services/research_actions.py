from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from assistant_core.services.mock_brand_research import run_mock_brand_watch
from assistant_core.services.mock_description_generator import create_mock_draft


@dataclass(frozen=True)
class ResearchActionResult:
    message: str
    payload: Any = None


def run_mock_brand_watch_action(
    profile_id: int,
    *,
    runner: Callable[[int], Any] = run_mock_brand_watch,
) -> ResearchActionResult:
    job = runner(profile_id)
    return ResearchActionResult(message=job.result_summary, payload=job)


def generate_mock_draft_action(
    perfume_id: int,
    *,
    runner: Callable[[int], Any] = create_mock_draft,
) -> ResearchActionResult:
    draft = runner(perfume_id)
    return ResearchActionResult(
        message="Pending draft generated from approved claims.",
        payload=draft,
    )
