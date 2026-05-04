from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from assistant_linking.models import FragranticaProduct
from assistant_linking.models import FragranticaProductLink
from assistant_linking.utils.text import normalize_alias_value
from catalog.models import Brand, Perfume, get_or_create_collection
from prices.services.catalog_review import apply_fragrantica_identity_to_perfume
from prices.services.catalog_review import normalize_catalogue_collection_name


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}


@dataclass
class FragranticaPromotionIssue:
    row: int
    message: str


@dataclass
class FragranticaPromotionSummary:
    scanned: int = 0
    exported: int = 0
    created_sources: int = 0
    updated_sources: int = 0
    linked_sources: int = 0
    created_perfumes: int = 0
    updated_perfumes: int = 0
    skipped: int = 0
    issues: list[FragranticaPromotionIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _normalized_source_value(value: str) -> str:
    return normalize_alias_value(value or "").replace("&", "and")


def _source_payload(source: FragranticaProduct) -> dict[str, Any]:
    return {
        "id": source.id,
        "brand_name": source.brand_name,
        "normalized_brand_name": source.normalized_brand_name
        or _normalized_source_value(source.brand_name),
        "name": source.name,
        "normalized_name": source.normalized_name
        or _normalized_source_value(source.name),
        "collection_name": source.collection_name,
        "audience": source.audience,
        "release_year": source.release_year,
        "source_path": source.source_path,
        "source_url": source.source_url,
        "source_domain": source.source_domain,
        "match_status": source.match_status,
    }


def _target_payload(perfume: Perfume) -> dict[str, Any]:
    return {
        "perfume_id": perfume.id,
        "brand_id": perfume.brand_id,
        "brand_name": perfume.brand.name,
        "name": perfume.name,
        "concentration": perfume.concentration,
        "collection_name": perfume.collection_name,
        "audience": perfume.audience,
        "release_year": perfume.release_year,
    }


def build_fragrantica_catalogue_link_export(
    *,
    brand_name: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    queryset = (
        FragranticaProduct.objects.select_related(
            "matched_perfume", "matched_perfume__brand"
        )
        .filter(
            match_status=FragranticaProduct.STATUS_LINKED,
            matched_perfume__isnull=False,
        )
        .order_by("brand_name", "collection_name", "name", "id")
    )
    if brand_name:
        queryset = queryset.filter(brand_name__iexact=brand_name)
    if limit is not None:
        queryset = queryset[:limit]

    rows = [
        {
            "fragrantica": _source_payload(source),
            "target": _target_payload(source.matched_perfume),
            "link_type": FragranticaProductLink.LINK_TYPE_PRIMARY,
        }
        for source in queryset
    ]
    source_ids = [source.id for source in queryset]
    extra_links = (
        FragranticaProductLink.objects.select_related(
            "source",
            "source__matched_perfume",
            "perfume",
            "perfume__brand",
        )
        .filter(
            source_id__in=source_ids,
            link_type=FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
        )
        .order_by(
            "source__brand_name",
            "source__collection_name",
            "source__name",
            "perfume__brand__name",
            "perfume__name",
            "perfume__concentration",
        )
    )
    rows.extend(
        {
            "fragrantica": _source_payload(link.source),
            "target": _target_payload(link.perfume),
            "link_type": FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
        }
        for link in extra_links
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "perfumex.fragrantica_catalogue_links",
        "generated_at": timezone.now().isoformat(),
        "row_count": len(rows),
        "rows": rows,
    }


def write_fragrantica_catalogue_link_export(
    path: str | Path,
    *,
    brand_name: str = "",
    limit: int | None = None,
) -> FragranticaPromotionSummary:
    bundle = build_fragrantica_catalogue_link_export(
        brand_name=brand_name,
        limit=limit,
    )
    Path(path).write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return FragranticaPromotionSummary(
        scanned=bundle["row_count"],
        exported=bundle["row_count"],
    )


def load_fragrantica_catalogue_link_export(path: str | Path) -> dict[str, Any]:
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    if bundle.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"Unsupported Fragrantica catalogue link export schema: "
            f"{bundle.get('schema_version')!r}"
        )
    if bundle.get("kind") != "perfumex.fragrantica_catalogue_links":
        raise ValueError("Not a PerfumeX Fragrantica catalogue link export.")
    if not isinstance(bundle.get("rows"), list):
        raise ValueError("Fragrantica catalogue link export is missing rows.")
    return bundle


def _resolve_target_perfume(
    target: dict[str, Any],
    *,
    create_missing_perfumes: bool,
) -> tuple[Perfume | None, bool]:
    perfume_id = target.get("perfume_id")
    perfume = None
    if perfume_id:
        perfume = Perfume.objects.select_related("brand").filter(pk=perfume_id).first()
    if perfume is None:
        perfume = (
            Perfume.objects.select_related("brand")
            .filter(
                brand__name=target.get("brand_name", ""),
                name=target.get("name", ""),
                concentration=target.get("concentration", ""),
            )
            .first()
        )
    if perfume is not None or not create_missing_perfumes:
        return perfume, False

    brand_name = (target.get("brand_name") or "").strip()
    perfume_name = (target.get("name") or "").strip()
    if not brand_name or not perfume_name:
        return None, False
    brand, _brand_created = Brand.objects.get_or_create(name=brand_name)
    return (
        Perfume.objects.create(
            brand=brand,
            name=perfume_name,
            concentration=(target.get("concentration") or "").strip(),
            collection_name=normalize_catalogue_collection_name(
                target.get("collection_name") or ""
            ),
            audience=(target.get("audience") or "").strip(),
            release_year=target.get("release_year") or None,
        ),
        True,
    )


def _upsert_fragrantica_source(
    source_payload: dict[str, Any],
    target_perfume: Perfume,
    *,
    set_primary_link: bool = True,
) -> tuple[FragranticaProduct, bool, bool]:
    normalized_brand = source_payload.get(
        "normalized_brand_name"
    ) or _normalized_source_value(source_payload.get("brand_name", ""))
    normalized_name = source_payload.get("normalized_name") or _normalized_source_value(
        source_payload.get("name", "")
    )
    source, created = FragranticaProduct.objects.get_or_create(
        normalized_brand_name=normalized_brand,
        normalized_name=normalized_name,
        source_path=source_payload.get("source_path", ""),
        defaults={
            "brand_name": source_payload.get("brand_name", ""),
            "name": source_payload.get("name", ""),
            "collection_name": source_payload.get("collection_name", ""),
            "audience": source_payload.get("audience", ""),
            "release_year": source_payload.get("release_year"),
            "source_url": source_payload.get("source_url", ""),
            "source_domain": source_payload.get("source_domain", "fragrantica.com"),
            "matched_perfume": target_perfume if set_primary_link else None,
            "match_status": FragranticaProduct.STATUS_LINKED,
        },
    )
    if created:
        return source, True, False

    changed_fields = []
    collection = get_or_create_collection(
        target_perfume.brand, source_payload.get("collection_name", "")
    )
    field_names = (
        "brand_name",
        "name",
        "collection_name",
        "audience",
        "release_year",
        "source_url",
        "source_domain",
    )
    for field_name in field_names:
        next_value = source_payload.get(field_name)
        if next_value is None and field_name != "release_year":
            next_value = ""
        if getattr(source, field_name) != next_value:
            setattr(source, field_name, next_value)
            changed_fields.append(field_name)
    if collection and source.collection_id != collection.id:
        source.collection = collection
        changed_fields.append("collection")
    if set_primary_link and source.matched_perfume_id != target_perfume.id:
        source.matched_perfume = target_perfume
        changed_fields.append("matched_perfume")
    if source.match_status != FragranticaProduct.STATUS_LINKED:
        source.match_status = FragranticaProduct.STATUS_LINKED
        changed_fields.append("match_status")
    if changed_fields:
        source.normalized_brand_name = normalized_brand
        source.normalized_name = normalized_name
        changed_fields.extend(
            ["normalized_brand_name", "normalized_name", "updated_at"]
        )
        source.save(update_fields=changed_fields)
    return source, False, bool(changed_fields)


def import_fragrantica_catalogue_link_export(
    path: str | Path,
    *,
    apply: bool = False,
    create_missing_perfumes: bool = False,
) -> FragranticaPromotionSummary:
    bundle = load_fragrantica_catalogue_link_export(path)
    summary = FragranticaPromotionSummary(scanned=len(bundle["rows"]))

    def apply_rows() -> None:
        for index, row in enumerate(bundle["rows"], start=1):
            source_payload = row.get("fragrantica") or {}
            target_payload = row.get("target") or {}
            if source_payload.get("match_status") != FragranticaProduct.STATUS_LINKED:
                summary.skipped += 1
                continue
            link_type = row.get("link_type") or FragranticaProductLink.LINK_TYPE_PRIMARY
            set_primary_link = (
                link_type != FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA
            )

            perfume, perfume_created = _resolve_target_perfume(
                target_payload,
                create_missing_perfumes=create_missing_perfumes,
            )
            if perfume is None:
                summary.skipped += 1
                summary.issues.append(
                    FragranticaPromotionIssue(
                        index,
                        "Target catalogue perfume was not found.",
                    )
                )
                continue
            if not apply:
                summary.linked_sources += 1
                continue

            source, source_created, source_updated = _upsert_fragrantica_source(
                source_payload,
                perfume,
                set_primary_link=set_primary_link,
            )
            FragranticaProductLink.objects.update_or_create(
                source=source,
                perfume=perfume,
                defaults={
                    "link_type": (
                        FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA
                        if link_type == FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA
                        else FragranticaProductLink.LINK_TYPE_PRIMARY
                    ),
                    "note": (
                        "Promoted reviewed rare second Our Products link."
                        if link_type == FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA
                        else ""
                    ),
                },
            )
            changed_fields = apply_fragrantica_identity_to_perfume(source, perfume)
            if perfume_created:
                summary.created_perfumes += 1
            if changed_fields:
                summary.updated_perfumes += 1
            if source_created:
                summary.created_sources += 1
            elif source_updated:
                summary.updated_sources += 1
            summary.linked_sources += 1

    if apply:
        with transaction.atomic():
            apply_rows()
    else:
        apply_rows()
    return summary
