from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from assistant_linking import forms, models
from assistant_linking.services.catalog_matcher import (
    candidate_matches,
    rule_impact,
    similar_supplier_rows,
)
from assistant_linking.services.garbage import GARBAGE_MODIFIER
from assistant_linking.services.garbage import clear_garbage_keyword_cache
from assistant_linking.services.garbage import normalize_garbage_keyword
from assistant_linking.services.normalizer import parse_supplier_product, save_parse
from assistant_linking.services.smart_search import normalize_query
from catalog.models import Brand, Perfume, PerfumeVariant, compact_decimal_text
from prices.models import SupplierProduct


@dataclass(frozen=True)
class AcceptCatalogCandidateResult:
    accepted: bool
    message_level: str
    message: str


@dataclass(frozen=True)
class NormalizationActionResult:
    success: bool
    message_level: str
    message: str


@dataclass(frozen=True)
class AliasFormActionResult:
    success: bool
    message_level: str
    message: str
    product: object
    form: object | None = None
    form_context_key: str = ""


@dataclass(frozen=True)
class TeachParseActionResult:
    success: bool
    message_level: str
    message: str
    product: object
    form: object | None = None
    form_context_key: str = ""
    updated_similar: int = 0


def normalization_detail_queryset(
    supplier_product_model=SupplierProduct,
):
    return supplier_product_model.objects.select_related(
        "supplier",
        "catalog_perfume__brand",
        "catalog_variant",
    )


def suggested_catalog_candidate(canonical_perfume, catalog_candidates):
    if canonical_perfume or not catalog_candidates:
        return None
    best_candidate = catalog_candidates[0]
    if (
        best_candidate.score >= 80
        and "concentration differs" in best_candidate.conflicts
    ):
        return best_candidate
    return None


def find_existing_product_alias(
    parsed, product, product_alias_model=models.ProductAlias
):
    product_alias_text = parsed.product_name_text or product.name
    if not parsed.normalized_brand_id or not product_alias_text:
        return None
    alias_queryset = product_alias_model.objects.filter(
        brand=parsed.normalized_brand,
        active=True,
    ).filter(
        Q(alias_text__iexact=product_alias_text)
        | Q(canonical_text__iexact=product_alias_text),
        Q(supplier=product.supplier) | Q(supplier__isnull=True),
    )
    return alias_queryset.order_by("supplier_id", "priority").first()


def manual_decision_snapshot(decision):
    return {
        "id": decision.id,
        "supplier_product_id": decision.supplier_product_id,
        "perfume_id": decision.perfume_id,
        "variant_id": decision.variant_id,
        "decision_type": decision.decision_type,
        "reason": decision.reason,
        "apply_to_similar": decision.apply_to_similar,
        "created_by_id": decision.created_by_id,
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
    }


def record_manual_link_decision(
    *,
    supplier_product,
    perfume_id,
    variant_id,
    decision_type,
    reason,
    apply_to_similar,
    created_by,
    allow_overwrite=False,
):
    previous = None
    if allow_overwrite:
        previous = (
            models.ManualLinkDecision.objects.select_for_update()
            .filter(supplier_product=supplier_product)
            .order_by("-created_at", "-id")
            .first()
        )
    decision = models.ManualLinkDecision.objects.create(
        supplier_product=supplier_product,
        perfume_id=perfume_id or None,
        variant_id=variant_id or None,
        decision_type=decision_type,
        reason=reason,
        apply_to_similar=apply_to_similar,
        created_by=created_by,
    )
    if previous:
        models.ManualLinkDecisionAudit.objects.create(
            previous_pk=previous.pk,
            previous_decision_json=manual_decision_snapshot(previous),
            replaced_by=decision,
        )
    return decision


def build_teach_initial(
    *,
    product,
    parsed,
    teaching_perfume,
    teaching_variant,
    brand_alias_text,
    product_alias_text,
    existing_blockers,
    teaching_form_class=forms.ParseTeachingForm,
):
    return {
        "supplier_brand_text": brand_alias_text,
        "brand_name": (
            teaching_perfume.brand.name
            if teaching_perfume
            else (
                parsed.normalized_brand.name
                if parsed.normalized_brand_id
                else parsed.detected_brand_text
            )
        ),
        "supplier_product_text": product_alias_text,
        "product_name": (
            teaching_perfume.name if teaching_perfume else parsed.product_name_text
        ),
        "product_excluded_terms": existing_blockers,
        "supplier_concentration_text": parsed.concentration,
        "concentration": (
            teaching_perfume.concentration if teaching_perfume else parsed.concentration
        ),
        "supplier_size_text": parsed.raw_size_text or product.size,
        "size_ml": (
            compact_decimal_text(teaching_variant.size_ml)
            if teaching_variant and teaching_variant.size_ml
            else compact_decimal_text(parsed.size_ml) if parsed.size_ml else None
        ),
        "supplier_audience_text": parsed.supplier_gender_hint,
        "audience": (
            teaching_perfume.audience
            if teaching_perfume and teaching_perfume.audience
            else parsed.supplier_gender_hint
        ),
        "supplier_type_text": parsed.variant_type,
        "variant_type": (
            teaching_variant.variant_type
            if teaching_variant and teaching_variant.variant_type
            else parsed.variant_type
        ),
        "supplier_packaging_text": parsed.packaging,
        "packaging": (
            teaching_variant.packaging
            if teaching_variant and teaching_variant.packaging
            else parsed.packaging
        ),
        "alias_scope": teaching_form_class.SCOPE_GLOBAL,
        "lock_parse": True,
        "reparse_similar": False,
    }


def build_catalog_reference_context(
    *,
    brand_model=Brand,
    perfume_model=Perfume,
    variant_model=PerfumeVariant,
):
    return {
        "catalog_brands": brand_model.objects.filter(is_active=True).order_by("name")[
            :1000
        ],
        "catalog_perfumes": perfume_model.objects.select_related("brand").order_by(
            "brand__name", "name"
        )[:2000],
        "catalog_packagings": variant_model.objects.exclude(packaging="")
        .values_list("packaging", flat=True)
        .distinct()
        .order_by("packaging"),
        "catalog_variant_types": variant_model.objects.exclude(variant_type="")
        .values_list("variant_type", flat=True)
        .distinct()
        .order_by("variant_type"),
    }


def get_saved_or_preview_parse(
    product,
    *,
    parse_preview_builder=parse_supplier_product,
):
    existing = getattr(product, "assistant_parse", None)
    return existing or parse_preview_builder(product)


def build_parsed_product_detail_context(
    *,
    product,
    hidden_keywords: list[str],
    context_overrides=None,
    parse_builder=get_saved_or_preview_parse,
    parse_saver=None,
    candidate_builder=candidate_matches,
    similar_rows_builder=similar_supplier_rows,
    rule_impact_builder=rule_impact,
    alias_finder=find_existing_product_alias,
    teaching_form_class=forms.ParseTeachingForm,
    catalog_reference_builder=build_catalog_reference_context,
):
    context_overrides = context_overrides or {}
    parsed = parse_saver(product) if parse_saver is not None else parse_builder(product)
    parsed_is_saved = isinstance(parsed, models.ParsedSupplierProduct)
    canonical_perfume = product.catalog_perfume
    canonical_variant = product.catalog_variant
    is_garbage = GARBAGE_MODIFIER in (parsed.modifiers or [])
    catalog_candidates = (
        [] if is_garbage or parsed.is_set else candidate_builder(parsed)
    )
    suggested_candidate = suggested_catalog_candidate(
        canonical_perfume, catalog_candidates
    )
    product_alias_text = parsed.product_name_text or product.name
    brand_alias_text = parsed.detected_brand_text or product.brand
    existing_alias = alias_finder(parsed, product)
    existing_blockers = existing_alias.excluded_terms if existing_alias else ""
    teaching_perfume = canonical_perfume or (
        suggested_candidate.perfume if suggested_candidate else None
    )
    teaching_variant = canonical_variant or (
        suggested_candidate.variant if suggested_candidate else None
    )
    teach_initial = build_teach_initial(
        product=product,
        parsed=parsed,
        teaching_perfume=teaching_perfume,
        teaching_variant=teaching_variant,
        brand_alias_text=brand_alias_text,
        product_alias_text=product_alias_text,
        existing_blockers=existing_blockers,
        teaching_form_class=teaching_form_class,
    )
    return {
        "parsed": parsed,
        "parsed_is_saved": parsed_is_saved,
        "teach_form": context_overrides.get("teach_form")
        or teaching_form_class(initial=teach_initial),
        "brand_alias_form": context_overrides.get("brand_alias_form"),
        "product_alias_form": context_overrides.get("product_alias_form"),
        "catalog_candidates": catalog_candidates,
        "is_garbage": is_garbage,
        "suggested_catalog_candidate": suggested_candidate,
        "similar_rows": similar_rows_builder(
            product,
            parsed,
            hidden_terms=hidden_keywords,
        ),
        "rule_impact": rule_impact_builder(
            product,
            brand_alias_text,
            product_alias_text,
            existing_blockers,
            hidden_terms=hidden_keywords,
        ),
        **catalog_reference_builder(),
    }


def _upsert_catalog_accept_aliases(
    *,
    product,
    parsed,
    perfume,
    supplier,
    excluded_terms,
    brand_alias_model=models.BrandAlias,
    product_alias_model=models.ProductAlias,
):
    brand_alias_text = (
        parsed.detected_brand_text or product.brand or perfume.brand.name
    ).strip()
    product_alias_text = (parsed.product_name_text or perfume.name).strip()

    if brand_alias_text:
        brand_alias_model.objects.update_or_create(
            brand=perfume.brand,
            supplier=supplier,
            alias_text=brand_alias_text,
            defaults={
                "normalized_alias": normalize_query(brand_alias_text),
                "priority": 10 if supplier else 50,
                "active": True,
            },
        )
    if product_alias_text:
        product_alias_model.objects.update_or_create(
            brand=perfume.brand,
            perfume=perfume,
            supplier=supplier,
            alias_text=product_alias_text,
            defaults={
                "canonical_text": perfume.name,
                "concentration": perfume.concentration,
                "audience": perfume.audience,
                "excluded_terms": excluded_terms,
                "priority": 10 if supplier else 50,
                "active": True,
            },
        )
    return brand_alias_text, product_alias_text


def _lock_parse_to_catalog_candidate(*, parsed, perfume, variant, brand_alias_text):
    parsed.normalized_brand = perfume.brand
    parsed.detected_brand_text = brand_alias_text or perfume.brand.name
    parsed.product_name_text = perfume.name
    parsed.concentration = perfume.concentration
    parsed.supplier_gender_hint = perfume.audience
    if variant:
        parsed.size_ml = variant.size_ml
        parsed.packaging = variant.packaging
        parsed.variant_type = variant.variant_type
        parsed.is_tester = variant.is_tester
    parsed.confidence = 100
    parsed.warnings = []
    parsed.locked_by_human = True
    parsed.last_parsed_at = timezone.now()
    parsed.save()


def accept_catalog_candidate(
    *,
    supplier_product_id,
    perfume_id,
    variant_id=None,
    alias_scope="",
    excluded_terms="",
    user,
    perfume_model=Perfume,
    variant_model=PerfumeVariant,
    supplier_product_model=SupplierProduct,
    suggestion_model=models.LinkSuggestion,
    brand_alias_model=models.BrandAlias,
    product_alias_model=models.ProductAlias,
    parse_saver=save_parse,
    decision_recorder=record_manual_link_decision,
):
    perfume = get_object_or_404(
        perfume_model.objects.select_related("brand"), pk=perfume_id
    )
    variant = None
    if variant_id:
        variant = get_object_or_404(variant_model, pk=variant_id, perfume=perfume)

    with transaction.atomic():
        product = get_object_or_404(
            supplier_product_model.objects.select_for_update().select_related(
                "supplier"
            ),
            pk=supplier_product_id,
        )
        suggestion = (
            suggestion_model.objects.select_for_update()
            .filter(
                supplier_product=product,
                suggested_perfume=perfume,
                suggested_variant=variant,
            )
            .order_by("-created_at", "-id")
            .first()
        )
        if suggestion and suggestion.status != suggestion_model.STATUS_PENDING:
            return AcceptCatalogCandidateResult(
                accepted=False,
                message_level="warning",
                message="This suggestion was already handled by another user.",
            )

        parsed = parse_saver(product)
        supplier = (
            product.supplier
            if alias_scope == forms.ParseTeachingForm.SCOPE_SUPPLIER
            else None
        )
        brand_alias_text, _product_alias_text = _upsert_catalog_accept_aliases(
            product=product,
            parsed=parsed,
            perfume=perfume,
            supplier=supplier,
            excluded_terms=excluded_terms,
            brand_alias_model=brand_alias_model,
            product_alias_model=product_alias_model,
        )
        _lock_parse_to_catalog_candidate(
            parsed=parsed,
            perfume=perfume,
            variant=variant,
            brand_alias_text=brand_alias_text,
        )

        had_link = bool(product.catalog_perfume_id or product.catalog_variant_id)
        product.catalog_perfume = perfume
        product.catalog_variant = variant
        product.save(update_fields=["catalog_perfume", "catalog_variant", "updated_at"])

        decision_recorder(
            supplier_product=product,
            perfume_id=perfume.id,
            variant_id=variant.id if variant else None,
            decision_type=(
                models.ManualLinkDecision.DECISION_APPROVE_VARIANT
                if variant
                else models.ManualLinkDecision.DECISION_APPROVE_PERFUME
            ),
            reason="Accepted from normalization catalogue candidates.",
            apply_to_similar=False,
            created_by=user,
            allow_overwrite=had_link,
        )
        if suggestion:
            suggestion.status = suggestion_model.STATUS_APPROVED
            suggestion.reviewed_by = user
            suggestion.reviewed_at = timezone.now()
            suggestion.save(
                update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
            )
    return AcceptCatalogCandidateResult(
        accepted=True,
        message_level="success",
        message="Catalogue candidate accepted and parse locked.",
    )


def reparse_supplier_product(
    *,
    supplier_product_id,
    force=False,
    supplier_product_model=SupplierProduct,
    parse_saver=save_parse,
):
    product = get_object_or_404(supplier_product_model, pk=supplier_product_id)
    parse_saver(product, force=force)
    return NormalizationActionResult(
        success=True,
        message_level="success",
        message="Product parsed.",
    )


def save_garbage_keywords_for_product(
    *,
    supplier_product_id,
    keywords_text,
    user,
    supplier_product_model=SupplierProduct,
    global_rule_model=None,
    keyword_normalizer=normalize_garbage_keyword,
    cache_clearer=clear_garbage_keyword_cache,
    parse_saver=save_parse,
):
    from assistant_core.models import GlobalRule

    global_rule_model = global_rule_model or GlobalRule
    product = get_object_or_404(
        supplier_product_model.objects.select_related("supplier"),
        pk=supplier_product_id,
    )
    keywords = keyword_normalizer(keywords_text)
    if not keywords:
        return NormalizationActionResult(
            success=False,
            message_level="error",
            message="Add at least one garbage keyword.",
        )

    for keyword in keywords.splitlines():
        global_rule_model.objects.update_or_create(
            rule_kind="garbage_keyword",
            scope_type="global",
            rule_text=keyword,
            defaults={
                "title": f"Garbage keyword: {keyword}",
                "scope_value": "",
                "priority": 10,
                "confidence": 100,
                "active": True,
                "approved": True,
                "created_by": user,
            },
        )
    cache_clearer()
    parse_saver(product, force=True)
    return NormalizationActionResult(
        success=True,
        message_level="success",
        message="Garbage keyword saved and this row was reparsed.",
    )


def lock_supplier_parse(
    *,
    supplier_product_id,
    parsed_model=models.ParsedSupplierProduct,
):
    parsed = get_object_or_404(parsed_model, supplier_product_id=supplier_product_id)
    parsed.locked_by_human = True
    parsed.save(update_fields=["locked_by_human", "updated_at"])
    return NormalizationActionResult(
        success=True,
        message_level="success",
        message="Parse locked.",
    )


def mark_invalid_fields_for_a11y(form):
    for field_name in form.errors:
        if field_name in form.fields:
            form.fields[field_name].widget.attrs[
                "aria-describedby"
            ] = f"id_{field_name}_errors"
            form.fields[field_name].widget.attrs["aria-invalid"] = "true"


def save_brand_alias_for_product(
    *,
    supplier_product_id,
    post_data,
    supplier_product_model=SupplierProduct,
    form_class=forms.BrandAliasForm,
):
    product = get_object_or_404(
        supplier_product_model.objects.select_related("supplier"),
        pk=supplier_product_id,
    )
    form = form_class(post_data)
    if form.is_valid():
        form.save()
        return AliasFormActionResult(
            success=True,
            message_level="success",
            message="Brand alias saved.",
            product=product,
        )
    mark_invalid_fields_for_a11y(form)
    return AliasFormActionResult(
        success=False,
        message_level="error",
        message="Brand alias was not saved.",
        product=product,
        form=form,
        form_context_key="brand_alias_form",
    )


def save_product_alias_for_product(
    *,
    supplier_product_id,
    post_data,
    supplier_product_model=SupplierProduct,
    form_class=forms.ProductAliasForm,
):
    product = get_object_or_404(
        supplier_product_model.objects.select_related("supplier"),
        pk=supplier_product_id,
    )
    form = form_class(post_data)
    if form.is_valid():
        form.save()
        return AliasFormActionResult(
            success=True,
            message_level="success",
            message="Product alias saved.",
            product=product,
        )
    mark_invalid_fields_for_a11y(form)
    return AliasFormActionResult(
        success=False,
        message_level="error",
        message="Product alias was not saved.",
        product=product,
        form=form,
        form_context_key="product_alias_form",
    )


def selected_similar_ids_from_values(values):
    return {int(value) for value in values if str(value).isdigit()}


def apply_teaching_to_parsed(
    *,
    parsed,
    brand,
    brand_alias_text,
    product_name,
    data,
):
    parsed.normalized_brand = brand
    parsed.detected_brand_text = brand_alias_text
    parsed.product_name_text = product_name
    parsed.concentration = data.get("concentration") or ""
    parsed.size_ml = data.get("size_ml")
    parsed.raw_size_text = (data.get("supplier_size_text") or "").strip()
    parsed.supplier_gender_hint = data.get("audience") or ""
    parsed.packaging = (data.get("packaging") or "").strip().lower()
    parsed.variant_type = (data.get("variant_type") or "").strip().lower()
    parsed.is_sample = parsed.variant_type == "sample"
    parsed.is_travel = parsed.variant_type == "travel"
    parsed.is_set = parsed.variant_type == "set"
    parsed.is_tester = parsed.variant_type == "tester" or "tester" in parsed.packaging
    parsed.confidence = 100
    parsed.warnings = []
    parsed.locked_by_human = bool(data.get("lock_parse"))
    parsed.last_parsed_at = timezone.now()
    parsed.save()


def reparse_selected_similar_products(
    *,
    product,
    selected_ids,
    similar_terms,
    supplier_product_model=SupplierProduct,
    parse_saver=save_parse,
):
    similar_filter = Q()
    for term in [term.strip() for term in similar_terms if term and term.strip()]:
        similar_filter |= Q(name__icontains=term)
    if not similar_filter or not selected_ids:
        return 0
    updated_similar = 0
    similar = supplier_product_model.objects.filter(
        similar_filter, pk__in=selected_ids
    ).exclude(pk=product.pk)[:500]
    for similar_product in similar:
        parse_saver(similar_product)
        updated_similar += 1
    return updated_similar


def teach_parse_for_product(
    *,
    supplier_product_id,
    post_data,
    selected_similar_values=(),
    supplier_product_model=SupplierProduct,
    brand_model=Brand,
    brand_alias_model=models.BrandAlias,
    product_alias_model=models.ProductAlias,
    form_class=forms.ParseTeachingForm,
    parse_saver=save_parse,
):
    product = get_object_or_404(
        supplier_product_model.objects.select_related("supplier"),
        pk=supplier_product_id,
    )
    parsed = parse_saver(product)
    form = form_class(post_data)
    if not form.is_valid():
        mark_invalid_fields_for_a11y(form)
        return TeachParseActionResult(
            success=False,
            message_level="error",
            message="Teaching form has invalid values.",
            product=product,
            form=form,
            form_context_key="teach_form",
        )

    data = form.cleaned_data
    brand_name = data["brand_name"].strip()
    product_name = data["product_name"].strip()
    brand = brand_model.objects.filter(name__iexact=brand_name).first()
    if not brand:
        brand = brand_model.objects.create(name=brand_name)
    supplier = (
        product.supplier if data["alias_scope"] == form_class.SCOPE_SUPPLIER else None
    )

    brand_alias_text = (data.get("supplier_brand_text") or brand_name).strip()
    if brand_alias_text:
        brand_alias_model.objects.get_or_create(
            brand=brand,
            supplier=supplier,
            alias_text=brand_alias_text,
            defaults={
                "normalized_alias": normalize_query(brand_alias_text),
                "priority": 10 if supplier else 50,
                "active": True,
            },
        )

    product_alias_text = (data.get("supplier_product_text") or product_name).strip()
    if product_alias_text:
        product_alias_model.objects.update_or_create(
            brand=brand,
            supplier=supplier,
            alias_text=product_alias_text,
            defaults={
                "canonical_text": product_name,
                "concentration": data.get("concentration") or "",
                "audience": data.get("audience") or "",
                "excluded_terms": (data.get("product_excluded_terms") or "").strip(),
                "priority": 10 if supplier else 50,
                "active": True,
            },
        )

    apply_teaching_to_parsed(
        parsed=parsed,
        brand=brand,
        brand_alias_text=brand_alias_text,
        product_name=product_name,
        data=data,
    )

    updated_similar = 0
    if data.get("reparse_similar"):
        updated_similar = reparse_selected_similar_products(
            product=product,
            selected_ids=selected_similar_ids_from_values(selected_similar_values),
            similar_terms=[
                brand_alias_text,
                product_alias_text,
                data.get("supplier_concentration_text"),
                data.get("supplier_size_text"),
            ],
            supplier_product_model=supplier_product_model,
            parse_saver=parse_saver,
        )

    return TeachParseActionResult(
        success=True,
        message_level="success",
        message=(
            "Teaching saved. This product is now parsed as "
            f"{brand.name} / {product_name}. Updated {updated_similar} selected preview rows."
        ),
        product=product,
        updated_similar=updated_similar,
    )
