from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

from django.db.models import Q
from django.test import SimpleTestCase

from assistant_linking.services.group_queries import (
    build_group_detail_context,
    group_detail_items,
    group_queue_queryset,
)


class GroupQueryServiceTests(SimpleTestCase):
    def test_group_detail_items_selects_related_item_fields(self):
        selected_items = MagicMock()
        group = SimpleNamespace(items=MagicMock())
        group.items.select_related.return_value = selected_items

        result = group_detail_items(group)

        self.assertIs(result, selected_items)
        group.items.select_related.assert_called_once_with(
            "supplier_product",
            "supplier_product__supplier",
            "parsed_product",
        )

    def test_build_group_detail_context_combines_items_and_last_action(self):
        group = SimpleNamespace(id=1)
        user = SimpleNamespace(id=2)
        items = ["item"]
        action = SimpleNamespace(id=3)
        items_builder = MagicMock(return_value=items)
        latest_action_finder = MagicMock(return_value=action)

        context = build_group_detail_context(
            group=group,
            user=user,
            items_builder=items_builder,
            latest_action_finder=latest_action_finder,
        )

        items_builder.assert_called_once_with(group)
        latest_action_finder.assert_called_once_with(user)
        self.assertEqual(
            context,
            {
                "items": items,
                "last_link_action": action,
            },
        )

    def test_group_queue_queryset_applies_base_related_annotation_and_ordering(self):
        ordered_queryset = MagicMock()
        annotated_queryset = MagicMock()
        annotated_queryset.order_by.return_value = ordered_queryset
        related_queryset = MagicMock()
        related_queryset.annotate.return_value = annotated_queryset
        manager = MagicMock()
        manager.select_related.return_value = related_queryset
        group_model = SimpleNamespace(objects=manager)

        result = group_queue_queryset(group_model=group_model)

        self.assertIs(result, ordered_queryset)
        manager.select_related.assert_called_once_with(
            "normalized_brand",
            "candidate_perfume",
            "candidate_variant",
        )
        related_queryset.annotate.assert_called_once_with(item_count=ANY)
        annotated_queryset.filter.assert_not_called()
        annotated_queryset.order_by.assert_called_once_with(
            "status",
            "-confidence",
            "canonical_name",
        )

    def test_group_queue_queryset_filters_by_status_and_brand(self):
        ordered_queryset = MagicMock()
        brand_filtered_queryset = MagicMock()
        brand_filtered_queryset.order_by.return_value = ordered_queryset
        status_filtered_queryset = MagicMock()
        status_filtered_queryset.filter.return_value = brand_filtered_queryset
        annotated_queryset = MagicMock()
        annotated_queryset.filter.return_value = status_filtered_queryset
        related_queryset = MagicMock()
        related_queryset.annotate.return_value = annotated_queryset
        manager = MagicMock()
        manager.select_related.return_value = related_queryset
        group_model = SimpleNamespace(objects=manager)

        result = group_queue_queryset(
            status="open",
            brand="hero",
            group_model=group_model,
        )

        self.assertIs(result, ordered_queryset)
        annotated_queryset.filter.assert_called_once_with(status="open")
        brand_filter = status_filtered_queryset.filter.call_args.args[0]
        self.assertIsInstance(brand_filter, Q)
        brand_filtered_queryset.order_by.assert_called_once_with(
            "status",
            "-confidence",
            "canonical_name",
        )
