from __future__ import annotations

import csv
import html
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from assistant_linking.models import FragranticaProduct
from assistant_linking.utils.text import normalize_alias_value


ALL_FRAGRANCES_SECTION = "All Fragrances"
AUDIENCE_BY_CLASS = {
    "tw-listview-item-female": "Women",
    "tw-listview-item-male": "Men",
    "tw-listview-item-unisex": "Unisex",
}
AUDIENCE_TEXT_PATTERNS = (
    ("женский", "Women"),
    ("female", "Women"),
    ("for women", "Women"),
    ("мужской", "Men"),
    ("male", "Men"),
    ("for men", "Men"),
    ("унисекс", "Unisex"),
    ("unisex", "Unisex"),
)


def clean_scraped_text(value: str) -> str:
    text = html.unescape(re.sub(r"\s+", " ", value or "")).strip()
    if not text:
        return ""
    repaired = text
    for _ in range(3):
        candidate = ""
        for codec in ("latin1", "cp1252"):
            try:
                candidate = repaired.encode(codec).decode("utf-8")
                break
            except UnicodeError:
                continue
        if not candidate:
            break
        if candidate == repaired:
            break
        repaired = candidate
    return re.sub(r"\s+", " ", repaired).strip()


def canonical_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", clean_scraped_text(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return normalize_alias_value(text).replace("&", "and")


def audience_from_classes(classes: set[str]) -> str:
    for class_name, audience in AUDIENCE_BY_CLASS.items():
        if class_name in classes:
            return audience
    return ""


def audience_from_text(value: str) -> str:
    text = clean_scraped_text(value).lower()
    for pattern, audience in AUDIENCE_TEXT_PATTERNS:
        if pattern in text:
            return audience
    return ""


@dataclass
class CatalogItem:
    brand_name: str
    name: str
    collection_name: str = ""
    audience: str = ""
    release_year: int | None = None
    source_path: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return canonical_key(self.brand_name), canonical_key(self.name)


@dataclass
class CatalogImportSummary:
    brand_name: str = ""
    source_items: list[CatalogItem] = field(default_factory=list)
    collections: set[str] = field(default_factory=set)
    existing_fragrantica_products: list[FragranticaProduct] = field(default_factory=list)
    missing_items: list[CatalogItem] = field(default_factory=list)
    created_fragrantica_products: list[FragranticaProduct] = field(default_factory=list)
    updated_fragrantica_products: list[FragranticaProduct] = field(default_factory=list)


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
            self._current_item = {
                "collection_name": self.current_collection,
                "audience": audience_from_classes(classes)
                or audience_from_text(attr_map.get("aria-label", ""))
                or audience_from_text(attr_map.get("title", "")),
            }
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
            audience=raw.get("audience", ""),
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
    summary.brand_name = resolved_brand_name
    summary.collections = {item.collection_name for item in items if item.collection_name}

    for item in items:
        normalized_brand = canonical_key(item.brand_name or resolved_brand_name)
        normalized_name = canonical_key(item.name)
        source_path = item.source_path or ""
        fragrantica_product = FragranticaProduct.objects.filter(
            normalized_brand_name=normalized_brand,
            normalized_name=normalized_name,
            source_path=source_path,
        ).first()
        if fragrantica_product is None:
            summary.missing_items.append(item)
            if apply:
                fragrantica_product = FragranticaProduct.objects.create(
                    brand_name=item.brand_name or resolved_brand_name,
                    normalized_brand_name=normalized_brand,
                    name=item.name,
                    normalized_name=normalized_name,
                    collection_name=item.collection_name,
                    audience=item.audience,
                    release_year=item.release_year,
                    source_path=source_path,
                    source_url=source_url,
                )
                summary.created_fragrantica_products.append(fragrantica_product)
        else:
            summary.existing_fragrantica_products.append(fragrantica_product)
            update_fields = []
            if fragrantica_product.brand_name != (item.brand_name or resolved_brand_name):
                fragrantica_product.brand_name = item.brand_name or resolved_brand_name
                update_fields.append("brand_name")
            if fragrantica_product.name != item.name:
                fragrantica_product.name = item.name
                update_fields.append("name")
            if item.collection_name and fragrantica_product.collection_name != item.collection_name:
                fragrantica_product.collection_name = item.collection_name
                update_fields.append("collection_name")
            if item.release_year and fragrantica_product.release_year != item.release_year:
                fragrantica_product.release_year = item.release_year
                update_fields.append("release_year")
            if item.audience and fragrantica_product.audience != item.audience:
                fragrantica_product.audience = item.audience
                update_fields.append("audience")
            if source_url and fragrantica_product.source_url != source_url:
                fragrantica_product.source_url = source_url
                update_fields.append("source_url")
            if apply and update_fields:
                fragrantica_product.normalized_brand_name = normalized_brand
                fragrantica_product.normalized_name = normalized_name
                update_fields.extend(["normalized_brand_name", "normalized_name"])
                update_fields.append("updated_at")
                fragrantica_product.save(update_fields=update_fields)
                summary.updated_fragrantica_products.append(fragrantica_product)
    return summary


def write_missing_report(path: str | Path, items: list[CatalogItem]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["brand", "collection", "name", "audience", "release_year", "source_path"])
        for item in items:
            writer.writerow(
                [
                    item.brand_name,
                    item.collection_name,
                    item.name,
                    item.audience,
                    item.release_year or "",
                    item.source_path,
                ]
            )
