from __future__ import annotations

from dataclasses import dataclass

from assistant_core import models
from assistant_linking.services.garbage import (
    clear_garbage_keyword_cache,
    normalize_garbage_keyword,
)
from assistant_linking.services.parser_rules import (
    PARSER_RULE_KINDS,
    clear_parser_rule_cache,
    normalize_parser_terms,
    validate_parser_rule_text,
)


@dataclass(frozen=True)
class KnowledgeActionResult:
    success: bool
    message: str
    section: str


def create_garbage_keyword_rules(post_data, user) -> KnowledgeActionResult:
    keywords = normalize_garbage_keyword(post_data.get("keywords", ""))
    if not keywords:
        return KnowledgeActionResult(
            success=False,
            message="Add at least one keyword.",
            section="garbage_keywords",
        )

    keyword_lines = keywords.splitlines()
    for keyword in keyword_lines:
        models.GlobalRule.objects.update_or_create(
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
    clear_garbage_keyword_cache()
    return KnowledgeActionResult(
        success=True,
        message=f"Saved {len(keyword_lines)} garbage keyword(s).",
        section="garbage_keywords",
    )


def create_parser_term_rules(post_data, user) -> KnowledgeActionResult:
    rule_kind = post_data.get("rule_kind", "").strip()
    raw_terms = post_data.get("terms", "")
    if rule_kind not in set(PARSER_RULE_KINDS):
        return KnowledgeActionResult(
            success=False,
            message="Choose a valid parser rule kind.",
            section="parser_terms",
        )

    if rule_kind in {"regex_preprocess", "parser_audience_term"}:
        terms = [line.strip() for line in raw_terms.splitlines() if line.strip()]
    else:
        terms = normalize_parser_terms(raw_terms)
    if not terms:
        return KnowledgeActionResult(
            success=False,
            message="Add at least one parser term.",
            section="parser_terms",
        )

    errors = [
        error for term in terms if (error := validate_parser_rule_text(rule_kind, term))
    ]
    if errors:
        return KnowledgeActionResult(
            success=False,
            message=errors[0],
            section="parser_terms",
        )

    for term in terms:
        models.GlobalRule.objects.update_or_create(
            rule_kind=rule_kind,
            scope_type="global",
            rule_text=term,
            defaults={
                "title": f"{rule_kind}: {term}",
                "scope_value": "",
                "priority": 50,
                "confidence": 100,
                "active": True,
                "approved": True,
                "created_by": user,
            },
        )
    clear_parser_rule_cache()
    return KnowledgeActionResult(
        success=True,
        message=f"Saved {len(terms)} parser rule(s).",
        section="parser_terms",
    )


def disable_rule(rule, *, is_global: bool) -> KnowledgeActionResult:
    rule.active = False
    rule.save(update_fields=["active", "updated_at"])
    if is_global and rule.rule_kind in {"garbage_keyword", "exclude_keyword"}:
        clear_garbage_keyword_cache()
    if is_global and (
        rule.rule_kind.startswith("parser_") or rule.rule_kind == "regex_preprocess"
    ):
        clear_parser_rule_cache()
    return KnowledgeActionResult(
        success=True,
        message="Rule disabled.",
        section="brand_aliases",
    )


def create_teaching_rule_from_decision(post_data, decision, user):
    scope = post_data.get("scope", "supplier")
    title = f"Decision rule from {decision.supplier_product_id}"
    rule_text = decision.reason or decision.decision_type
    if scope == "global":
        return models.GlobalRule.objects.create(
            title=title,
            rule_kind="linking",
            scope_type="global",
            rule_text=rule_text,
            approved=False,
            created_by=user,
        )
    return models.SupplierRule.objects.create(
        supplier=decision.supplier_product.supplier,
        title=title,
        rule_kind="linking",
        rule_text=rule_text,
        approved=False,
        created_by=user,
    )
