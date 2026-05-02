from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from assistant_core.services.catalog_queries import (
    build_catalog_brand_queryset,
    build_catalog_form_context,
    build_catalog_variant_queryset,
)


class FakeValuesQuery:
    def __init__(self, label):
        self.label = label
        self.values_call = None

    def values_list(self, *args, **kwargs):
        self.values_call = (args, kwargs)
        return self

    def distinct(self):
        return self

    def order_by(self, field):
        return f"{self.label}:{field}"


class FakeManager:
    def __init__(self):
        self.exclude_calls = []

    def exclude(self, **kwargs):
        self.exclude_calls.append(kwargs)
        return FakeValuesQuery(next(iter(kwargs)))


class CatalogQueryServiceTests(SimpleTestCase):
    def test_build_catalog_form_context_collects_distinct_dropdown_values(self):
        perfume_manager = FakeManager()
        variant_manager = FakeManager()
        perfume_model = SimpleNamespace(objects=perfume_manager)
        variant_model = SimpleNamespace(objects=variant_manager)

        context = build_catalog_form_context(
            perfume_model=perfume_model,
            variant_model=variant_model,
        )

        self.assertEqual(
            context,
            {
                "concentrations": "concentration:concentration",
                "audiences": "audience:audience",
                "packagings": "packaging:packaging",
                "variant_types": "variant_type:variant_type",
            },
        )
        self.assertEqual(
            perfume_manager.exclude_calls,
            [{"concentration": ""}, {"audience": ""}],
        )
        self.assertEqual(
            variant_manager.exclude_calls,
            [{"packaging": ""}, {"variant_type": ""}],
        )

    def test_build_catalog_brand_queryset_filters_when_query_is_present(self):
        result = object()
        filtered = MagicMock()
        filtered.order_by.return_value = result
        queryset = MagicMock()
        queryset.filter.return_value = filtered
        manager = MagicMock()
        manager.annotate.return_value = queryset
        brand_model = SimpleNamespace(objects=manager)

        returned = build_catalog_brand_queryset(" mont ", brand_model=brand_model)

        self.assertIs(returned, result)
        manager.annotate.assert_called_once()
        queryset.filter.assert_called_once()
        filtered.order_by.assert_called_once_with("name")

    def test_build_catalog_variant_queryset_skips_filter_without_query(self):
        result = object()
        queryset = MagicMock()
        queryset.order_by.return_value = result
        manager = MagicMock()
        manager.select_related.return_value = queryset
        variant_model = SimpleNamespace(objects=manager)

        returned = build_catalog_variant_queryset("", variant_model=variant_model)

        self.assertIs(returned, result)
        manager.select_related.assert_called_once_with("perfume", "perfume__brand")
        queryset.filter.assert_not_called()
        queryset.order_by.assert_called_once_with(
            "perfume__brand__name", "perfume__name", "size_ml"
        )
