from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.utils import timezone

from assistant_core.models import BrandWatchProfile, DetectedChange, ResearchJob, SourceSnapshot


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "brand_watch"


def _load_fixture(profile: BrandWatchProfile) -> dict:
    path = FIXTURE_DIR / f"{profile.brand.slug}.json"
    if not path.exists():
        path = FIXTURE_DIR / "default.json"
    if not path.exists():
        return {
            "sources": [
                {
                    "url": profile.official_url or profile.brand.official_url or "https://example.com",
                    "title": f"{profile.brand.name} official source",
                    "source_type": "official",
                    "summary": "Mock research source.",
                    "facts": {},
                }
            ],
            "changes": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def run_mock_brand_watch(profile_id: int) -> ResearchJob:
    profile = BrandWatchProfile.objects.select_related("brand").get(pk=profile_id)
    now = timezone.now()
    payload = _load_fixture(profile)
    job = ResearchJob.objects.create(
        job_type="brand_watch_mock",
        status=ResearchJob.STATUS_RUNNING,
        brand=profile.brand,
        query=profile.brand.name,
        context_json={"profile_id": profile.id, "trusted_sources": profile.trusted_sources_json},
        started_at=now,
    )

    created_changes = 0
    for source in payload.get("sources", []):
        raw = json.dumps(source, sort_keys=True, ensure_ascii=True)
        SourceSnapshot.objects.get_or_create(
            brand_profile=profile,
            url=source.get("url") or profile.official_url or "https://example.com",
            content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            defaults={
                "title": source.get("title", ""),
                "source_type": source.get("source_type", "fixture"),
                "extracted_text": source.get("text", ""),
                "extracted_summary": source.get("summary", ""),
                "raw_facts_json": source.get("facts", {}),
                "checked_at": now,
                "fetch_status": "ok",
            },
        )
    for change in payload.get("changes", []):
        DetectedChange.objects.create(
            brand_profile=profile,
            change_type=change.get("change_type", "changed_field"),
            field_name=change.get("field_name", ""),
            old_value_json=change.get("old_value", {}),
            new_value_json=change.get("new_value", {}),
            explanation=change.get("explanation", "Mock detected change"),
            confidence=int(change.get("confidence", 70)),
            source_urls_json=change.get("source_urls", []),
        )
        created_changes += 1

    profile.last_checked_at = now
    profile.last_success_at = now
    profile.save(update_fields=["last_checked_at", "last_success_at", "updated_at"])
    job.status = ResearchJob.STATUS_FINISHED
    job.result_summary = f"Mock scan created {created_changes} detected changes."
    job.raw_result_json = payload
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "result_summary", "raw_result_json", "finished_at", "updated_at"])
    return job
