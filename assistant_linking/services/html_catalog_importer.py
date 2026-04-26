from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from assistant_linking.models import BrandAlias, ProductAlias
from assistant_linking.utils.text import normalize_alias_value
from catalog.models import Brand, Perfume, Source


ALL_FRAGRANCES_SECTION = "All Fragrances"
DEFAULT_SOURCE_TYPE = "community"


def clean_scraped_text(value: str) -> str:
    text = html.unescape(re.sub(r"\s+", " ", value or "")).strip()
    if not text:
        return ""
    try:
        repaired = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        repaired = text
    return re.sub(r"\s+", " ", repaired).strip()


def canonical_key(value: str) -> str:
    return normalize_alias_value(clean_scraped_text(value)).replace("&", "and")


@dataclass
class CatalogItem:
    brand_name: str
    name: str
    collection_name: str = ""
    release_year: int | None = None
    source_path: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return canonical_key(self.brand_name), canonical_key(self.name)


@dataclass
class CatalogImportSummary:
    brand: Brand | None = None
    source_items: list[CatalogItem] = field(default_factory=list)
    collections: set[str] = field(default_factory=set)
    matched_perfumes: list[Perfume] = field(default_factory=list)
    missing_items: list[CatalogItem] = field(default_factory=list)
    created_perfumes: list[Perfume] = field(default_factory=list)
    updated_perfumes: list[Perfume] = field(default_factory=list)
    created_aliases: int = 0
    updated_aliases: int = 0
    created_sources: int = 0


class FragranticaBrandCatalogParser(HTMLParser):
    """Parser for saved brand catalogue HTML.

    Rule: `h2.tw-gridlist-section-title` sets the active collection section.
    Each following `a.prefumeHbox` product row is assigned to that collection
    until another section title appears. `All Fragrances` is treated as an
    index section and does not overwrite a more specific collection.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current_collection = ""
        self.items_by_key: dict[tuple[str, str], CatalogItem] = {}
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._current_item: dict[str, str] | None = None
        self._current_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_map = {name: value or "" for name, value in attrs}
        classes = set((attr_map.get("class") or "").split())
        if tag == "h2" and "tw-gridlist-section-title" in classes:
            self._start_capture("section")
            return
        if tag == "a" and "prefumeHbox" in classes:
            self._current_item = {"collection_name": self.current_collection}
            self._current_href = attr_map.get("href", "")
            return
        if self._current_item is None:
            return
        if tag == "h3" and "tw-perfume-title" in classes:
            self._start_capture("name")
        elif tag == "p" and "tw-perfume-designer" in classes:
            self._start_capture("brand")
        elif tag == "span" and "tw-year-badge" in classes:
            self._start_capture("year")

    def handle_data(self, data: str):
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str):
        if self._capture == "section" and tag == "h2":
            self.current_collection = clean_scraped_text("".join(self._buffer))
            self._clear_capture()
            return
        if self._current_item is not None:
            if self._capture == "name" and tag == "h3":
                self._current_item["name"] = clean_scraped_text("".join(self._buffer))
                self._clear_capture()
            elif self._capture == "brand" and tag == "p":
                self._current_item["brand_name"] = clean_scraped_text("".join(self._buffer))
                self._clear_capture()
            elif self._capture == "year" and tag == "span":
                self._current_item["year"] = clean_scraped_text("".join(self._buffer))
                self._clear_capture()
            elif tag == "a":
                self._finish_item()

    def _start_capture(self, name: str):
        self._capture = name
        self._buffer = []

    def _clear_capture(self):
        self._capture = None
        self._buffer = []

    def _finish_item(self):
        raw = self._current_item or {}
        self._current_item = None
        name = raw.get("name", "")
        brand_name = raw.get("brand_name", "")
        if not name or not brand_name:
            return
        year_text = raw.get("year", "")
        year = int(year_text) if re.fullmatch(r"(?:19|20)\d{2}", year_text) else None
        collection_name = raw.get("collection_name", "")
        if collection_name == ALL_FRAGRANCES_SECTION:
            collection_name = ""
        item = CatalogItem(
            brand_name=brand_name,
            name=name,
            collection_name=collection_name,
            release_year=year,
            source_path=self._current_href,
        )
        existing = self.items_by_key.get(item.key)
        if existing is None or (item.collection_name and not existing.collection_name):
            self.items_by_key[item.key] = item


def parse_brand_catalog_html(raw_html: str) -> list[CatalogItem]:
    parser = FragranticaBrandCatalogParser()
    parser.feed(raw_html)
    parser.close()
    return sorted(parser.items_by_key.values(), key=lambda item: (item.brand_name, item.collection_name, item.name))


def parse_brand_catalog_file(path: str | Path) -> list[CatalogItem]:
    return parse_brand_catalog_html(Path(path).read_text(encoding="utf-8", errors="replace"))


def import_brand_catalog(
    items: list[CatalogItem],
    *,
    brand_name: str | None = None,
    apply: bool = False,
    create_missing_catalog: bool = False,
    create_aliases: bool = False,
    source_url: str = "",
) -> CatalogImportSummary:
    summary = CatalogImportSummary(source_items=items)
    if not items:
        return summary
    resolved_brand_name = clean_scraped_text(brand_name or items[0].brand_name)
    if apply:
        brand, _ = Brand.objects.get_or_create(name=resolved_brand_name)
    else:
        brand = Brand.objects.filter(name__iexact=resolved_brand_name).first()
        if brand is None:
            brand = Brand(name=resolved_brand_name)
    summary.brand = brand
    summary.collections = {item.collection_name for item in items if item.collection_name}

    matched_keys: set[tuple[str, str]] = set()
    existing_perfumes = list(Perfume.objects.select_related("brand").filter(brand__name__iexact=resolved_brand_name))
    existing_by_key = {canonical_key(perfume.name): perfume for perfume in existing_perfumes}

    for item in items:
        perfume = existing_by_key.get(canonical_key(item.name))
        if perfume is None:
            summary.missing_items.append(item)
            if apply and create_missing_catalog:
                perfume = Perfume.objects.create(
                    brand=brand,
                    name=item.name,
                    collection_name=item.collection_name,
                    release_year=item.release_year,
                    verification_status=Perfume.VERIFICATION_REVIEW,
                )
                existing_by_key[canonical_key(item.name)] = perfume
                summary.created_perfumes.append(perfume)
            else:
                continue
        else:
            summary.matched_perfumes.append(perfume)
            matched_keys.add(item.key)
            update_fields = []
            if item.collection_name and perfume.collection_name != item.collection_name:
                perfume.collection_name = item.collection_name
                update_fields.append("collection_name")
            if item.release_year and perfume.release_year != item.release_year:
                perfume.release_year = item.release_year
                update_fields.append("release_year")
            if apply and update_fields:
                update_fields.append("updated_at")
                perfume.save(update_fields=update_fields)
                summary.updated_perfumes.append(perfume)
        if apply and source_url:
            source, created = Source.objects.get_or_create(
                perfume=perfume,
                url=source_url,
                defaults={
                    "title": f"{resolved_brand_name} catalogue",
                    "source_type": DEFAULT_SOURCE_TYPE,
                    "source_domain": "fragrantica.com",
                    "reliability": "medium",
                },
            )
            if created:
                summary.created_sources += 1
        if apply and create_aliases:
            _upsert_product_alias(summary, brand, perfume, item)

    if apply and create_aliases:
        _upsert_brand_alias(summary, brand, resolved_brand_name)
    summary.matched_perfumes = [perfume for perfume in summary.matched_perfumes if (canonical_key(perfume.brand.name), canonical_key(perfume.name)) in matched_keys]
    return summary


def _upsert_brand_alias(summary: CatalogImportSummary, brand: Brand, alias_text: str) -> None:
    _, created = BrandAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text=alias_text,
        defaults={"normalized_alias": normalize_alias_value(alias_text), "active": True, "priority": 80},
    )
    summary.created_aliases += int(created)
    summary.updated_aliases += int(not created)


def _upsert_product_alias(summary: CatalogImportSummary, brand: Brand, perfume: Perfume, item: CatalogItem) -> None:
    _, created = ProductAlias.objects.update_or_create(
        brand=brand,
        supplier=None,
        alias_text=item.name,
        defaults={
            "perfume": perfume,
            "canonical_text": perfume.name,
            "collection_name": item.collection_name,
            "audience": perfume.audience,
            "active": True,
            "priority": 80,
        },
    )
    summary.created_aliases += int(created)
    summary.updated_aliases += int(not created)


def write_missing_report(path: str | Path, items: list[CatalogItem]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["brand", "collection", "name", "release_year", "source_path"])
        for item in items:
            writer.writerow([item.brand_name, item.collection_name, item.name, item.release_year or "", item.source_path])
