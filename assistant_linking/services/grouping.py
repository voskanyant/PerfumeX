from __future__ import annotations

from decimal import Decimal

from assistant_linking.models import MatchGroup, MatchGroupItem, ParsedSupplierProduct
from assistant_linking.services.normalizer import save_parse
from prices.models import SupplierProduct


def build_group_key(parsed: ParsedSupplierProduct) -> str:
    size = ""
    if parsed.size_ml is not None:
        size = str(Decimal(parsed.size_ml).normalize())
    modifiers = "-".join(sorted(parsed.modifiers or []))
    parts = [
        str(parsed.normalized_brand_id or "brand-missing"),
        parsed.product_name_text or "name-missing",
        parsed.concentration or "",
        parsed.supplier_gender_hint or "",
        size,
        parsed.packaging or "",
        parsed.variant_type or "",
        modifiers,
    ]
    return "|".join(parts).lower()


def rebuild_groups(*, supplier_id: int | None = None, only_open: bool = False) -> int:
    products = SupplierProduct.objects.select_related("supplier").all()
    if supplier_id:
        products = products.filter(supplier_id=supplier_id)
    count = 0
    for product in products.iterator():
        parsed = save_parse(product)
        group_key = build_group_key(parsed)
        group, _ = MatchGroup.objects.get_or_create(
            group_key=group_key,
            defaults={
                "normalized_brand": parsed.normalized_brand,
                "canonical_name": parsed.product_name_text or parsed.supplier_product.name[:255],
                "concentration": parsed.concentration,
                "audience_hint": parsed.supplier_gender_hint,
                "size_ml": parsed.size_ml,
                "packaging": parsed.packaging,
                "variant_type": parsed.variant_type,
                "confidence": parsed.confidence,
            },
        )
        if only_open and group.status != MatchGroup.STATUS_OPEN:
            continue
        MatchGroupItem.objects.update_or_create(
            match_group=group,
            supplier_product=product,
            defaults={
                "parsed_product": parsed,
                "role": MatchGroupItem.ROLE_MEMBER,
                "match_score": parsed.confidence,
                "reasoning": "Deterministic parsed-field group key",
            },
        )
        count += 1
    return count
