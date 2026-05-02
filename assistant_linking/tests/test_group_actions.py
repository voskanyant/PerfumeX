from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from assistant_linking.services.group_actions import (
    apply_group_action,
    rebuild_group_memberships,
)


class GroupActionServiceTests(SimpleTestCase):
    def test_rebuild_group_memberships_returns_count_and_message(self):
        rebuilder = MagicMock(return_value=7)

        result = rebuild_group_memberships(only_open=True, rebuilder=rebuilder)

        rebuilder.assert_called_once_with(only_open=True)
        self.assertEqual(result.count, 7)
        self.assertEqual(result.message, "Rebuilt 7 group memberships.")

    def test_apply_group_action_excludes_selected_items(self):
        items = MagicMock()
        items.update.return_value = 2
        item_query = MagicMock()
        item_query.filter.return_value = items
        group = SimpleNamespace(items=MagicMock())
        group.items.select_for_update.return_value = item_query
        group_model = SimpleNamespace(objects=MagicMock())
        item_model = SimpleNamespace(ROLE_EXCLUDED="excluded", ROLE_SPLIT="split")

        with (
            patch(
                "assistant_linking.services.group_actions.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "assistant_linking.services.group_actions.get_object_or_404",
                return_value=group,
            ),
        ):
            result = apply_group_action(
                group_id=5,
                action="exclude",
                item_ids=["1", "2"],
                reason="not the same product",
                group_model=group_model,
                item_model=item_model,
            )

        group_model.objects.select_for_update.assert_called_once_with()
        item_query.filter.assert_called_once_with(id__in=["1", "2"])
        items.update.assert_called_once_with(
            role="excluded",
            reasoning="not the same product",
        )
        self.assertEqual(result.affected_count, 2)
        self.assertEqual(result.excluded_count, 2)

    def test_apply_group_action_splits_selected_items(self):
        new_group = SimpleNamespace(id=99)
        item = SimpleNamespace(
            supplier_product_id=12,
            match_group=None,
            role=None,
            reasoning="",
            save=MagicMock(),
        )
        item_query = MagicMock()
        item_query.filter.return_value = [item]
        group = SimpleNamespace(
            group_key="brand|scent",
            normalized_brand="Brand",
            canonical_name="Scent",
            concentration="edp",
            audience_hint="women",
            size_ml=50,
            packaging="tester",
            variant_type="spray",
            confidence=85,
            items=MagicMock(),
        )
        group.items.select_for_update.return_value = item_query
        group_model = SimpleNamespace(
            STATUS_OPEN="open",
            objects=MagicMock(create=MagicMock(return_value=new_group)),
        )
        item_model = SimpleNamespace(ROLE_EXCLUDED="excluded", ROLE_SPLIT="split")

        with (
            patch(
                "assistant_linking.services.group_actions.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "assistant_linking.services.group_actions.get_object_or_404",
                return_value=group,
            ),
        ):
            result = apply_group_action(
                group_id=5,
                action="split",
                item_ids=["1"],
                reason="operator split",
                group_model=group_model,
                item_model=item_model,
                now=lambda: datetime(2026, 5, 1, tzinfo=dt_timezone.utc),
            )

        group_model.objects.create.assert_called_once_with(
            group_key="brand|scent|split|12|1777593600.0",
            normalized_brand="Brand",
            canonical_name="Scent",
            concentration="edp",
            audience_hint="women",
            size_ml=50,
            packaging="tester",
            variant_type="spray",
            status="open",
            confidence=75,
        )
        self.assertIs(item.match_group, new_group)
        self.assertEqual(item.role, "split")
        self.assertEqual(item.reasoning, "operator split")
        item.save.assert_called_once_with()
        self.assertEqual(result.affected_count, 1)
        self.assertEqual(result.split_count, 1)

    def test_apply_group_action_ignores_unknown_action(self):
        item_query = MagicMock()
        item_query.filter.return_value = []
        group = SimpleNamespace(items=MagicMock())
        group.items.select_for_update.return_value = item_query
        group_model = SimpleNamespace(objects=MagicMock())

        with (
            patch(
                "assistant_linking.services.group_actions.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "assistant_linking.services.group_actions.get_object_or_404",
                return_value=group,
            ),
        ):
            result = apply_group_action(
                group_id=5,
                action="unknown",
                item_ids=["1"],
                group_model=group_model,
            )

        self.assertEqual(result.affected_count, 0)
