from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from assistant_linking.services.workbench import (
    build_product_workbench_context,
    product_link_suggestions,
    same_supplier_products,
    similar_products_for_group,
)


class WorkbenchServiceTests(SimpleTestCase):
    def test_build_product_workbench_context_combines_context_builders(self):
        product = SimpleNamespace(id=1)
        user = SimpleNamespace(id=2)
        parsed = SimpleNamespace(id=3)
        group = SimpleNamespace(id=4)
        similar = ["similar"]
        suggestions = ["suggestion"]
        same_supplier = ["same supplier"]
        action = SimpleNamespace(id=5)
        parse_saver = MagicMock(return_value=parsed)
        group_finder = MagicMock(return_value=group)
        similar_builder = MagicMock(return_value=similar)
        suggestions_builder = MagicMock(return_value=suggestions)
        same_supplier_builder = MagicMock(return_value=same_supplier)
        latest_action_finder = MagicMock(return_value=action)

        context = build_product_workbench_context(
            product=product,
            user=user,
            parse_saver=parse_saver,
            group_finder=group_finder,
            similar_builder=similar_builder,
            suggestions_builder=suggestions_builder,
            same_supplier_builder=same_supplier_builder,
            latest_action_finder=latest_action_finder,
        )

        parse_saver.assert_called_once_with(product)
        group_finder.assert_called_once_with(product)
        similar_builder.assert_called_once_with(group)
        suggestions_builder.assert_called_once_with(product)
        same_supplier_builder.assert_called_once_with(product)
        latest_action_finder.assert_called_once_with(user)
        self.assertEqual(
            context,
            {
                "parsed": parsed,
                "group": group,
                "similar": similar,
                "suggestions": suggestions,
                "same_supplier": same_supplier,
                "last_link_action": action,
            },
        )

    def test_similar_products_for_group_returns_none_queryset_without_group(self):
        none_queryset = MagicMock()
        manager = MagicMock()
        manager.none.return_value = none_queryset
        supplier_product_model = SimpleNamespace(objects=manager)

        result = similar_products_for_group(
            None,
            supplier_product_model=supplier_product_model,
        )

        self.assertIs(result, none_queryset)
        manager.none.assert_called_once_with()
        manager.filter.assert_not_called()

    def test_similar_products_for_group_selects_related_product_fields(self):
        selected_queryset = MagicMock()
        filtered_queryset = MagicMock()
        filtered_queryset.select_related.return_value = selected_queryset
        manager = MagicMock()
        manager.filter.return_value = filtered_queryset
        supplier_product_model = SimpleNamespace(objects=manager)
        group = SimpleNamespace(id=7)

        result = similar_products_for_group(
            group,
            supplier_product_model=supplier_product_model,
        )

        self.assertIs(result, selected_queryset)
        manager.filter.assert_called_once_with(assistant_group_items__match_group=group)
        filtered_queryset.select_related.assert_called_once_with(
            "supplier",
            "catalog_perfume",
            "catalog_variant",
        )

    def test_product_link_suggestions_limits_recent_suggestions(self):
        sliced_queryset = MagicMock()
        ordered_queryset = MagicMock()
        ordered_queryset.__getitem__.return_value = sliced_queryset
        related_queryset = MagicMock()
        related_queryset.order_by.return_value = ordered_queryset
        suggestions_manager = MagicMock()
        suggestions_manager.select_related.return_value = related_queryset
        product = SimpleNamespace(assistant_link_suggestions=suggestions_manager)

        result = product_link_suggestions(product)

        self.assertIs(result, sliced_queryset)
        suggestions_manager.select_related.assert_called_once_with(
            "suggested_perfume",
            "suggested_variant",
        )
        related_queryset.order_by.assert_called_once_with("-created_at")
        ordered_queryset.__getitem__.assert_called_once_with(slice(None, 10, None))

    def test_same_supplier_products_excludes_source_product_and_limits_rows(self):
        sliced_queryset = MagicMock()
        ordered_queryset = MagicMock()
        ordered_queryset.__getitem__.return_value = sliced_queryset
        excluded_queryset = MagicMock()
        excluded_queryset.order_by.return_value = ordered_queryset
        filtered_queryset = MagicMock()
        filtered_queryset.exclude.return_value = excluded_queryset
        manager = MagicMock()
        manager.filter.return_value = filtered_queryset
        supplier_product_model = SimpleNamespace(objects=manager)
        supplier = SimpleNamespace(id=9)
        product = SimpleNamespace(pk=11, supplier=supplier)

        result = same_supplier_products(
            product,
            supplier_product_model=supplier_product_model,
        )

        self.assertIs(result, sliced_queryset)
        manager.filter.assert_called_once_with(supplier=supplier)
        filtered_queryset.exclude.assert_called_once_with(pk=11)
        excluded_queryset.order_by.assert_called_once_with("-is_active", "name")
        ordered_queryset.__getitem__.assert_called_once_with(slice(None, 25, None))
