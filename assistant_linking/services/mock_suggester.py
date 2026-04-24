from __future__ import annotations

from django.db.models import Q

from assistant_core.services.context_builder import build_assistant_context
from assistant_linking.models import LinkSuggestion, MatchGroup, ParsedSupplierProduct
from assistant_linking.services.normalizer import save_parse
from catalog.models import Perfume, PerfumeVariant
from prices.models import SupplierProduct


def _candidate_perfumes(parsed: ParsedSupplierProduct):
    perfumes = Perfume.objects.select_related("brand").all()
    if parsed.normalized_brand_id:
        perfumes = perfumes.filter(brand=parsed.normalized_brand)
    if parsed.product_name_text:
        perfumes = perfumes.filter(Q(name__icontains=parsed.product_name_text) | Q(collection_name__icontains=parsed.product_name_text))
    return perfumes[:10]


def generate_link_suggestions(supplier_product_id: int, *, limit: int = 5) -> list[dict]:
    product = SupplierProduct.objects.select_related("supplier", "catalog_perfume", "catalog_variant").get(pk=supplier_product_id)
    parsed = save_parse(product)
    context = build_assistant_context(supplier_product_id=supplier_product_id)
    group = MatchGroup.objects.filter(items__supplier_product=product).first()
    suggestions: list[dict] = []

    for perfume in _candidate_perfumes(parsed):
        confidence = 35
        reasoning = []
        uncertainties = []
        rules_used = [rule["title"] for rule in context["global_rules"][:3] + context["supplier_rules"][:3]]
        if parsed.normalized_brand_id == perfume.brand_id:
            confidence += 25
            reasoning.append(f"{parsed.detected_brand_text or perfume.brand.name} -> {perfume.brand.name} via alias or exact brand")
        if parsed.product_name_text and parsed.product_name_text.lower() in perfume.name.lower():
            confidence += 20
            reasoning.append(f"{parsed.product_name_text} matched catalogue perfume name")
        if parsed.concentration and parsed.concentration == perfume.concentration:
            confidence += 10
            reasoning.append(f"{parsed.concentration.upper()} exact match")
        elif parsed.concentration:
            uncertainties.append("catalogue concentration differs or is missing")
        if not parsed.supplier_gender_hint:
            uncertainties.append("risk: gender not explicit in supplier row")

        variant = None
        if parsed.size_ml:
            variant = PerfumeVariant.objects.filter(perfume=perfume, size_ml=parsed.size_ml).first()
            if variant:
                confidence += 10
                reasoning.append(f"{parsed.size_ml:g} ml exact variant size match")
        suggestion = LinkSuggestion.objects.create(
            supplier_product=product,
            match_group=group,
            suggested_perfume=perfume,
            suggested_variant=variant,
            confidence=min(confidence, 100),
            reasoning="\n".join(reasoning) or "Deterministic catalogue candidate",
            rules_used_json=rules_used,
            uncertainties_json=uncertainties,
            source_engine="mock",
        )
        suggestions.append(
            {
                "id": suggestion.id,
                "suggested_perfume_id": perfume.id,
                "suggested_variant_id": variant.id if variant else None,
                "confidence": suggestion.confidence,
                "reasoning": reasoning,
                "rules_used": rules_used,
                "uncertainties": uncertainties,
            }
        )
        if len(suggestions) >= limit:
            break
    return suggestions
