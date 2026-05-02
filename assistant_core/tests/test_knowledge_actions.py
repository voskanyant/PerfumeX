from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import QueryDict
from django.test import TestCase

from assistant_core.models import GlobalRule
from assistant_core.services.knowledge_actions import (
    create_garbage_keyword_rules,
    create_parser_term_rules,
    create_teaching_rule_from_decision,
    disable_rule,
)
from assistant_linking.models import ManualLinkDecision
from assistant_linking.services.garbage import GARBAGE_KEYWORD_CACHE_KEY
from assistant_linking.services.parser_rules import PARSER_RULE_CACHE_KEY
from prices.models import Supplier, SupplierProduct


User = get_user_model()


class KnowledgeActionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", password="pass")

    def test_create_garbage_keyword_rules_normalizes_and_clears_cache(self):
        cache.set(GARBAGE_KEYWORD_CACHE_KEY, ["old-trash"], 300)

        result = create_garbage_keyword_rules(
            QueryDict("keywords=new-trash,new-trash%0D%0Abroken box"),
            self.user,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Saved 2 garbage keyword(s).")
        self.assertEqual(result.section, "garbage_keywords")
        self.assertIsNone(cache.get(GARBAGE_KEYWORD_CACHE_KEY))
        self.assertTrue(
            GlobalRule.objects.filter(
                rule_kind="garbage_keyword",
                rule_text="new-trash",
                active=True,
                approved=True,
                created_by=self.user,
            ).exists()
        )
        self.assertTrue(
            GlobalRule.objects.filter(
                rule_kind="garbage_keyword",
                rule_text="broken box",
            ).exists()
        )

    def test_create_garbage_keyword_rules_rejects_empty_input(self):
        initial_count = GlobalRule.objects.count()

        result = create_garbage_keyword_rules(QueryDict("keywords="), self.user)

        self.assertFalse(result.success)
        self.assertEqual(result.message, "Add at least one keyword.")
        self.assertEqual(GlobalRule.objects.count(), initial_count)

    def test_create_parser_term_rules_validates_and_saves_terms(self):
        cache.set(PARSER_RULE_CACHE_KEY, {"parser_refill_term": ["old"]}, 300)
        initial_terms = set(
            GlobalRule.objects.filter(rule_kind="parser_refill_term").values_list(
                "rule_text", flat=True
            )
        )

        result = create_parser_term_rules(
            QueryDict("rule_kind=parser_refill_term&terms=custom-refill;custom refill"),
            self.user,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Saved 2 parser rule(s).")
        self.assertEqual(result.section, "parser_terms")
        self.assertIsNone(cache.get(PARSER_RULE_CACHE_KEY))
        self.assertEqual(
            set(
                GlobalRule.objects.filter(rule_kind="parser_refill_term").values_list(
                    "rule_text", flat=True
                )
            )
            - initial_terms,
            {"custom-refill", "custom refill"},
        )

    def test_create_parser_term_rules_rejects_invalid_rule_text(self):
        initial_count = GlobalRule.objects.count()

        result = create_parser_term_rules(
            QueryDict("rule_kind=parser_audience_term&terms=bad audience row"),
            self.user,
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.message,
            "Audience aliases must use: alias => Display | men/women/unisex.",
        )
        self.assertEqual(GlobalRule.objects.count(), initial_count)

    def test_disable_rule_clears_related_rule_caches(self):
        cache.set(GARBAGE_KEYWORD_CACHE_KEY, ["old-trash"], 300)
        cache.set(PARSER_RULE_CACHE_KEY, {"parser_refill_term": ["old"]}, 300)
        rule = GlobalRule.objects.create(
            title="Garbage parser rule",
            rule_kind="garbage_keyword",
            scope_type="global",
            rule_text="old-trash",
            active=True,
        )

        result = disable_rule(rule, is_global=True)

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Rule disabled.")
        rule.refresh_from_db()
        self.assertFalse(rule.active)
        self.assertIsNone(cache.get(GARBAGE_KEYWORD_CACHE_KEY))
        self.assertEqual(
            cache.get(PARSER_RULE_CACHE_KEY), {"parser_refill_term": ["old"]}
        )

        parser_rule = GlobalRule.objects.create(
            title="Parser rule",
            rule_kind="parser_refill_term",
            scope_type="global",
            rule_text="refill",
            active=True,
        )
        disable_rule(parser_rule, is_global=True)
        self.assertIsNone(cache.get(PARSER_RULE_CACHE_KEY))

    def test_create_teaching_rule_from_decision_creates_supplier_rule_by_default(self):
        supplier = Supplier.objects.create(name="Supplier A", code="supplier-a")
        supplier_product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="supplier-a:test",
            name="Test Product",
        )
        decision = ManualLinkDecision.objects.create(
            supplier_product=supplier_product,
            decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
            reason="manual match",
        )

        rule = create_teaching_rule_from_decision(QueryDict(""), decision, self.user)

        self.assertEqual(rule.supplier, supplier)
        self.assertEqual(rule.title, f"Decision rule from {supplier_product.id}")
        self.assertEqual(rule.rule_kind, "linking")
        self.assertEqual(rule.rule_text, "manual match")
        self.assertFalse(rule.approved)
        self.assertEqual(rule.created_by, self.user)

    def test_create_teaching_rule_from_decision_can_create_global_rule(self):
        supplier = Supplier.objects.create(name="Supplier A", code="supplier-a")
        supplier_product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="supplier-a:test",
            name="Test Product",
        )
        decision = ManualLinkDecision.objects.create(
            supplier_product=supplier_product,
            decision_type=ManualLinkDecision.DECISION_REJECT,
            reason="",
        )

        rule = create_teaching_rule_from_decision(
            QueryDict("scope=global"), decision, self.user
        )

        self.assertIsInstance(rule, GlobalRule)
        self.assertEqual(rule.scope_type, "global")
        self.assertEqual(rule.rule_text, ManualLinkDecision.DECISION_REJECT)
        self.assertFalse(rule.approved)
