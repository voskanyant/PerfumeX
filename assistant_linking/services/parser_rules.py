from __future__ import annotations

import re
import logging

from django.core.cache import cache
from django.db import OperationalError, ProgrammingError

from assistant_linking.utils.text import normalize_alias_value


logger = logging.getLogger(__name__)
PARSER_RULE_CACHE_KEY = "assistant_linking:parser_rules:v1"
PARSER_RULE_KINDS = (
    "parser_tester_term",
    "parser_sample_term",
    "parser_mini_term",
    "parser_travel_term",
    "parser_set_term",
    "parser_refill_term",
    "parser_audience_term",
    "regex_preprocess",
)
TERM_RULE_KINDS = tuple(kind for kind in PARSER_RULE_KINDS if kind != "regex_preprocess")


def clear_parser_rule_cache():
    cache.delete(PARSER_RULE_CACHE_KEY)


def normalize_parser_terms(value: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in re.split(r"[,;\n]+", value or ""):
        normalized = normalize_alias_value(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
    return terms


def _parse_preprocess_rule(rule_text: str) -> tuple[str, str] | None:
    if "=>" not in (rule_text or ""):
        return None
    pattern, replacement = rule_text.split("=>", 1)
    pattern = pattern.strip()
    replacement = replacement.strip()
    if not pattern:
        return None
    return pattern, replacement


def _parse_audience_rule(rule_text: str) -> tuple[str, str, str] | None:
    if "=>" not in (rule_text or ""):
        return None
    alias, target = rule_text.split("=>", 1)
    alias = normalize_alias_value(alias)
    target = target.strip()
    if "|" in target:
        display, group = [part.strip() for part in target.split("|", 1)]
    else:
        display = target
        group = normalize_alias_value(display)
    group = normalize_alias_value(group)
    if not alias or not display or group not in {"men", "women", "unisex"}:
        return None
    return alias, display, group


def get_parser_rules() -> dict[str, list]:
    rules = cache.get(PARSER_RULE_CACHE_KEY)
    if rules is not None:
        return rules

    from assistant_core.models import GlobalRule

    rules = {kind: [] for kind in PARSER_RULE_KINDS}
    try:
        rows = (
            GlobalRule.objects.filter(active=True, approved=True, rule_kind__in=PARSER_RULE_KINDS)
            .order_by("priority", "title")
            .values_list("rule_kind", "rule_text")
        )
        for rule_kind, rule_text in rows:
            if rule_kind == "regex_preprocess":
                parsed = _parse_preprocess_rule(rule_text)
                if parsed:
                    rules[rule_kind].append(parsed)
                continue
            if rule_kind == "parser_audience_term":
                parsed = _parse_audience_rule(rule_text)
                if parsed:
                    rules[rule_kind].append(parsed)
                continue
            for term in normalize_parser_terms(rule_text):
                rules[rule_kind].append(term)
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("parser KB rules unavailable; using built-in parser defaults: %s", exc)
        return rules

    cache.set(PARSER_RULE_CACHE_KEY, rules, 300)
    return rules


def get_parser_terms(rule_kind: str) -> tuple[str, ...]:
    return tuple(get_parser_rules().get(rule_kind, ()))


def get_regex_preprocess_rules() -> tuple[tuple[str, str], ...]:
    return tuple(get_parser_rules().get("regex_preprocess", ()))


def get_audience_alias_rules() -> tuple[tuple[str, str, str], ...]:
    return tuple(get_parser_rules().get("parser_audience_term", ()))
