from __future__ import annotations

from assistant_core.models import GlobalRule, KnowledgeNote, SupplierRule


def _rule_dict(rule):
    return {
        "id": rule.id,
        "title": rule.title,
        "rule_kind": rule.rule_kind,
        "rule_text": rule.rule_text,
        "priority": rule.priority,
        "confidence": rule.confidence,
    }


def build_assistant_context(*, supplier_product_id: int | None = None, match_group_id: int | None = None) -> dict:
    from assistant_linking.models import BrandAlias, ManualLinkDecision, MatchGroup, ParsedSupplierProduct, ProductAlias
    from prices.models import SupplierProduct

    product = None
    parsed = None
    group = None
    if supplier_product_id:
        product = SupplierProduct.objects.select_related("supplier", "catalog_perfume", "catalog_variant").filter(pk=supplier_product_id).first()
        parsed = ParsedSupplierProduct.objects.select_related("normalized_brand").filter(supplier_product_id=supplier_product_id).first()
    if match_group_id:
        group = MatchGroup.objects.filter(pk=match_group_id).first()

    supplier_rules = SupplierRule.objects.filter(active=True, approved=True)
    notes = KnowledgeNote.objects.filter(active=True)
    if product:
        supplier_rules = supplier_rules.filter(supplier=product.supplier)
        notes = notes.filter(supplier__isnull=True) | notes.filter(supplier=product.supplier)
    if parsed and parsed.normalized_brand_id:
        notes = notes.filter(brand__isnull=True) | notes.filter(brand=parsed.normalized_brand)

    return {
        "supplier_product_id": supplier_product_id,
        "match_group_id": match_group_id,
        "parsed": {
            "brand": parsed.normalized_brand.name if parsed and parsed.normalized_brand_id else None,
            "name": parsed.product_name_text if parsed else None,
            "concentration": parsed.concentration if parsed else None,
            "size_ml": str(parsed.size_ml) if parsed and parsed.size_ml else None,
            "warnings": parsed.warnings if parsed else [],
        },
        "group": {"id": group.id, "key": group.group_key, "status": group.status} if group else None,
        "global_rules": [_rule_dict(rule) for rule in GlobalRule.objects.filter(active=True, approved=True).order_by("priority")[:50]],
        "supplier_rules": [_rule_dict(rule) for rule in supplier_rules.order_by("priority")[:50]],
        "knowledge_notes": list(notes.values("id", "category", "title", "content")[:50]),
        "brand_aliases": list(BrandAlias.objects.filter(active=True).values("id", "alias_text", "brand_id", "supplier_id")[:100]),
        "product_aliases": list(ProductAlias.objects.filter(active=True).values("id", "alias_text", "canonical_text", "supplier_id")[:100]),
        "manual_decisions": list(
            ManualLinkDecision.objects.filter(supplier_product_id=supplier_product_id).values("id", "decision_type", "reason", "perfume_id", "variant_id")[:25]
            if supplier_product_id
            else []
        ),
    }
