from __future__ import annotations

from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from assistant_core.models import GlobalRule
from assistant_core.services.knowledge import (
    ALIAS_SECTION_BRANDS,
    ALIAS_SECTION_PRODUCTS,
    SECTION_BRAND_ALIASES,
    SECTION_GLOBAL_RULES,
    build_aliases_context,
    build_knowledge_context,
)
from assistant_linking.models import BrandAlias, ProductAlias
from catalog.models import Brand, Perfume
from prices.models import Supplier


class KnowledgeContextServiceTests(TestCase):
    def test_build_knowledge_context_defaults_invalid_section_to_brand_aliases(self):
        brand = Brand.objects.create(name="Montale")
        alias = BrandAlias.objects.create(
            brand=brand,
            alias_text="mntl",
            normalized_alias="mntl",
            active=True,
        )
        BrandAlias.objects.create(
            brand=brand,
            alias_text="inactive",
            normalized_alias="inactive",
            active=False,
        )

        context = build_knowledge_context(QueryDict("section=bad&q=mntl"))

        self.assertEqual(context["active_section"], SECTION_BRAND_ALIASES)
        self.assertEqual(context["query"], "mntl")
        self.assertEqual(context["status"], "active")
        self.assertEqual(context["scope"], "all")
        self.assertEqual(list(context["items"]), [alias])
        self.assertEqual(context["page_obj"].number, 1)
        self.assertIn("parser_rule_kind_options", context)

    def test_build_knowledge_context_uses_rules_default_section(self):
        initial_global_rule_count = GlobalRule.objects.count()
        rule = GlobalRule.objects.create(
            title="Parser term",
            rule_kind="parser_mini_term",
            scope_type="global",
            rule_text="miniature",
            active=True,
        )
        GlobalRule.objects.create(
            title="Inactive parser term",
            rule_kind="parser_mini_term",
            scope_type="global",
            rule_text="hidden",
            active=False,
        )

        context = build_knowledge_context(
            QueryDict("q=miniature"),
            default_section=SECTION_GLOBAL_RULES,
        )

        self.assertEqual(context["active_section"], SECTION_GLOBAL_RULES)
        self.assertEqual(context["query"], "miniature")
        self.assertEqual(context["status"], "active")
        self.assertEqual(list(context["items"]), [rule])
        sections = {section["key"]: section["count"] for section in context["sections"]}
        self.assertEqual(sections[SECTION_GLOBAL_RULES], initial_global_rule_count + 2)

    def test_build_aliases_context_defaults_invalid_section_to_brand_aliases(self):
        brand = Brand.objects.create(name="Montale")
        alias = BrandAlias.objects.create(
            brand=brand,
            alias_text="mntl",
            normalized_alias="mntl",
            active=True,
        )
        BrandAlias.objects.create(
            brand=brand,
            alias_text="inactive",
            normalized_alias="inactive",
            active=False,
        )

        context = build_aliases_context(QueryDict("section=bad&q=mntl"))

        self.assertEqual(context["active_section"], ALIAS_SECTION_BRANDS)
        self.assertEqual(context["query"], "mntl")
        self.assertEqual(context["status"], "active")
        self.assertEqual(context["scope"], "all")
        self.assertEqual(list(context["items"]), [alias])
        self.assertEqual(
            str(context["create_url"]), reverse("assistant_core:brand_alias_create")
        )

    def test_build_aliases_context_filters_product_aliases_by_scope_and_query(self):
        supplier = Supplier.objects.create(name="Supplier A", code="supplier-a")
        other_supplier = Supplier.objects.create(name="Supplier B", code="supplier-b")
        brand = Brand.objects.create(name="Montale")
        perfume = Perfume.objects.create(brand=brand, name="Vanilla Extasy")
        alias = ProductAlias.objects.create(
            brand=brand,
            perfume=perfume,
            supplier=supplier,
            alias_text="vanilla tester",
            canonical_text="Vanilla Extasy",
            active=True,
        )
        ProductAlias.objects.create(
            brand=brand,
            perfume=perfume,
            supplier=other_supplier,
            alias_text="other alias",
            canonical_text="Other",
            active=True,
        )
        ProductAlias.objects.create(
            brand=brand,
            perfume=perfume,
            alias_text="inactive tester",
            canonical_text="Inactive",
            active=False,
        )

        context = build_aliases_context(
            QueryDict("section=products&q=tester&scope=supplier")
        )

        self.assertEqual(context["active_section"], ALIAS_SECTION_PRODUCTS)
        self.assertEqual(context["query"], "tester")
        self.assertEqual(context["status"], "active")
        self.assertEqual(context["scope"], "supplier")
        self.assertEqual(list(context["items"]), [alias])
        self.assertEqual(
            str(context["create_url"]),
            reverse("assistant_core:product_alias_create"),
        )
