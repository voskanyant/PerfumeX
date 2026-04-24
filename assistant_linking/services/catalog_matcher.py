from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from assistant_linking.models import ParsedSupplierProduct
from assistant_linking.services.normalizer import normalize_text
from catalog.models import Perfume, PerfumeVariant
from prices.models import SupplierProduct


@dataclass
class CatalogCandidate:
    perfume: Perfume
    variant: PerfumeVariant | None
    score: int
    reasons: list[str]
    conflicts: list[str]


def _tokens(value: str) -> set[str]:
    return {token for token in normalize_text(value).split() if len(token) > 1}


def _token_score(source: str, candidate: str) -> int:
    source_tokens = _tokens(source)
    candidate_tokens = _tokens(candidate)
    if not source_tokens or not candidate_tokens:
        return 0
    overlap = source_tokens & candidate_tokens
    return round(45 * (len(overlap) / max(len(candidate_tokens), 1)))


def _variant_score(parsed: ParsedSupplierProduct, variant: PerfumeVariant | None) -> tuple[int, list[str], list[str]]:
    if not variant:
        return 0, [], []
    score = 0
    reasons: list[str] = []
    conflicts: list[str] = []
    if parsed.size_ml and variant.size_ml:
        if Decimal(parsed.size_ml) == Decimal(variant.size_ml):
            score += 15
            reasons.append("size matches")
        else:
            conflicts.append("size differs")
    if parsed.variant_type and variant.variant_type:
        if parsed.variant_type == variant.variant_type:
            score += 8
            reasons.append("type matches")
        else:
            conflicts.append("type differs")
    if parsed.packaging and variant.packaging:
        if parsed.packaging == variant.packaging:
            score += 6
            reasons.append("packaging matches")
        else:
            conflicts.append("packaging differs")
    if parsed.is_tester == variant.is_tester:
        score += 4
    elif parsed.is_tester or variant.is_tester:
        conflicts.append("tester status differs")
    return score, reasons, conflicts


def candidate_matches(parsed: ParsedSupplierProduct, limit: int = 8) -> list[CatalogCandidate]:
    product_text = parsed.product_name_text or parsed.raw_name
    brand_text = parsed.normalized_brand.name if parsed.normalized_brand_id else parsed.detected_brand_text
    perfumes = Perfume.objects.select_related("brand").prefetch_related("variants")
    if parsed.normalized_brand_id:
        perfumes = perfumes.filter(brand=parsed.normalized_brand)
    elif brand_text:
        perfumes = perfumes.filter(brand__name__icontains=brand_text)

    candidates: list[CatalogCandidate] = []
    for perfume in perfumes[:1000]:
        score = _token_score(product_text, perfume.name)
        reasons: list[str] = []
        conflicts: list[str] = []
        if score:
            reasons.append("name tokens overlap")
        if brand_text and normalize_text(brand_text) == normalize_text(perfume.brand.name):
            score += 25
            reasons.append("brand matches")
        if parsed.concentration and perfume.concentration:
            if parsed.concentration == perfume.concentration:
                score += 12
                reasons.append("concentration matches")
            else:
                conflicts.append("concentration differs")
                score -= 8
        if parsed.supplier_gender_hint and perfume.audience:
            if parsed.supplier_gender_hint == perfume.audience:
                score += 5
                reasons.append("audience matches")
            else:
                conflicts.append("audience differs")

        variants = list(perfume.variants.all()) or [None]
        for variant in variants:
            variant_points, variant_reasons, variant_conflicts = _variant_score(parsed, variant)
            total = max(min(score + variant_points, 100), 0)
            candidates.append(
                CatalogCandidate(
                    perfume=perfume,
                    variant=variant,
                    score=total,
                    reasons=reasons + variant_reasons,
                    conflicts=conflicts + variant_conflicts,
                )
            )

    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)[:limit]


def similar_supplier_rows(product: SupplierProduct, parsed: ParsedSupplierProduct, limit: int = 25):
    terms = [parsed.detected_brand_text, parsed.product_name_text]
    queryset = SupplierProduct.objects.select_related("supplier").exclude(pk=product.pk)
    for term in [term for term in terms if term]:
        queryset = queryset.filter(name__icontains=term.split()[0])
    return queryset.order_by("-is_active", "supplier__name", "name")[:limit]


def rule_impact(product: SupplierProduct, brand_alias_text: str, product_alias_text: str, excluded_terms: str = "") -> dict:
    queryset = SupplierProduct.objects.exclude(pk=product.pk)
    if brand_alias_text:
        queryset = queryset.filter(name__icontains=brand_alias_text)
    if product_alias_text:
        queryset = queryset.filter(name__icontains=product_alias_text)
    excluded = [normalize_text(term) for term in excluded_terms.replace(";", ",").split(",") if normalize_text(term)]
    examples = []
    risky = 0
    for row in queryset[:50]:
        text = normalize_text(row.name)
        has_blocker = any(term in text for term in excluded)
        if has_blocker:
            risky += 1
        examples.append({"product": row, "blocked": has_blocker})
    return {"count": queryset.count(), "risky": risky, "examples": examples[:10]}
