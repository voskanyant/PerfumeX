from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from assistant_linking.services.link_actions import (
    build_bulk_link_accepted_payload,
    build_bulk_link_selection,
    build_bulk_link_status_payload,
    build_undo_link_payload,
    bulk_link_products,
    bulk_link_success_message,
    get_undoable_bulk_link_action,
    is_ajax_request,
    should_return_bulk_link_async_response,
    undo_link_action,
    undo_link_success_message,
)


class FakeLinkActionModel:
    ACTION_BULK_LINK = "bulk_link"
    ACTION_UNDO_BULK_LINK = "undo_bulk_link"
    objects = MagicMock()


class FakePost(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


class LinkActionServiceTests(SimpleTestCase):
    def test_build_bulk_link_selection_uses_selected_product_ids(self):
        source = SimpleNamespace(
            id=10,
            pk=10,
            catalog_perfume_id=None,
            catalog_variant_id=None,
        )
        manager = MagicMock()
        supplier_product_model = SimpleNamespace(objects=manager)

        with patch(
            "assistant_linking.services.link_actions.get_object_or_404",
            return_value=source,
        ):
            selection = build_bulk_link_selection(
                supplier_product_id=10,
                post_data=FakePost(
                    {
                        "perfume_id": "7",
                        "variant_id": "",
                        "supplier_product_ids": ["10", "bad", "11", "10"],
                        "confirm_overwrite": "1",
                    }
                ),
                supplier_product_model=supplier_product_model,
            )

        self.assertFalse(selection.has_error)
        self.assertEqual(selection.product_ids, [10, 11])
        self.assertEqual(selection.perfume_id, "7")
        self.assertIsNone(selection.variant_id)
        self.assertTrue(selection.allow_overwrite)
        self.assertFalse(selection.apply_to_similar)

    def test_build_bulk_link_selection_falls_back_to_source_product(self):
        source = SimpleNamespace(
            id=10,
            pk=10,
            catalog_perfume_id=7,
            catalog_variant_id=8,
        )
        supplier_product_model = SimpleNamespace(objects=MagicMock())

        with patch(
            "assistant_linking.services.link_actions.get_object_or_404",
            return_value=source,
        ):
            selection = build_bulk_link_selection(
                supplier_product_id=10,
                post_data=FakePost({}),
                supplier_product_model=supplier_product_model,
            )

        self.assertFalse(selection.has_error)
        self.assertEqual(selection.product_ids, [10])
        self.assertEqual(selection.perfume_id, 7)
        self.assertEqual(selection.variant_id, 8)

    def test_build_bulk_link_selection_requires_perfume(self):
        source = SimpleNamespace(
            id=10,
            pk=10,
            catalog_perfume_id=None,
            catalog_variant_id=None,
        )

        with patch(
            "assistant_linking.services.link_actions.get_object_or_404",
            return_value=source,
        ):
            selection = build_bulk_link_selection(
                supplier_product_id=10,
                post_data=FakePost({}),
                supplier_product_model=SimpleNamespace(objects=MagicMock()),
            )

        self.assertTrue(selection.has_error)
        self.assertIsNone(selection.error_status)
        self.assertEqual(
            selection.error_message,
            "Choose or approve a catalogue perfume before linking rows.",
        )

    def test_build_bulk_link_selection_requires_similar_confirmation(self):
        source = SimpleNamespace(
            id=10,
            pk=10,
            catalog_perfume_id=7,
            catalog_variant_id=None,
        )
        group = SimpleNamespace(id=20)
        queryset = MagicMock()
        queryset.order_by.return_value.values_list.return_value = [10, 11]
        supplier_manager = MagicMock()
        supplier_manager.filter.return_value = queryset
        supplier_product_model = SimpleNamespace(objects=supplier_manager)
        group_model = SimpleNamespace(objects=MagicMock())
        group_model.objects.filter.return_value.first.return_value = group

        with patch(
            "assistant_linking.services.link_actions.get_object_or_404",
            return_value=source,
        ):
            selection = build_bulk_link_selection(
                supplier_product_id=10,
                post_data=FakePost({"apply_to_similar": "1"}),
                supplier_product_model=supplier_product_model,
                group_model=group_model,
            )

        self.assertTrue(selection.has_error)
        self.assertEqual(selection.error_status, 409)
        self.assertEqual(selection.product_ids, [10, 11])
        self.assertEqual(
            selection.error_message,
            "Confirm apply_to_similar before linking 2 matched products.",
        )
        group_model.objects.filter.assert_called_once_with(
            items__supplier_product=source
        )
        supplier_manager.filter.assert_called_once_with(
            assistant_group_items__match_group=group
        )

    def test_build_bulk_link_selection_enforces_product_cap(self):
        source = SimpleNamespace(
            id=10,
            pk=10,
            catalog_perfume_id=7,
            catalog_variant_id=None,
        )
        product_ids = [10, 11, 12]

        with patch(
            "assistant_linking.services.link_actions.get_object_or_404",
            return_value=source,
        ):
            selection = build_bulk_link_selection(
                supplier_product_id=10,
                post_data=FakePost({"supplier_product_ids": product_ids}),
                supplier_product_model=SimpleNamespace(objects=MagicMock()),
                product_cap=2,
            )

        self.assertTrue(selection.has_error)
        self.assertEqual(selection.error_status, 409)
        self.assertEqual(selection.product_ids, product_ids)
        self.assertEqual(
            selection.error_message,
            "Bulk link matched 3 products; narrow scope to 2 or fewer.",
        )

    def test_build_bulk_link_status_payload_summarizes_action_payload(self):
        action = SimpleNamespace(
            id=99,
            payload_json={
                "status": "COMPLETE",
                "matched": "5",
                "linked": "3",
                "skipped": "1",
            },
        )

        payload = build_bulk_link_status_payload(action, undo_url="/undo/99/")

        self.assertEqual(
            payload,
            {
                "job_id": 99,
                "status": "COMPLETE",
                "matched": 5,
                "linked": 3,
                "skipped": 1,
                "processed": 4,
                "percent": 100,
                "undo_url": "/undo/99/",
            },
        )

    def test_build_bulk_link_status_payload_defaults_empty_payload(self):
        action = SimpleNamespace(id=99, payload_json={})

        payload = build_bulk_link_status_payload(action, undo_url="/undo/99/")

        self.assertEqual(payload["status"], "COMPLETE")
        self.assertEqual(payload["matched"], 0)
        self.assertEqual(payload["linked"], 0)
        self.assertEqual(payload["skipped"], 0)
        self.assertEqual(payload["processed"], 0)
        self.assertEqual(payload["percent"], 0)

    def test_should_return_bulk_link_async_response_for_large_or_ajax_requests(self):
        self.assertTrue(
            should_return_bulk_link_async_response(
                product_ids=list(range(21)),
                headers={},
            )
        )
        self.assertTrue(
            should_return_bulk_link_async_response(
                product_ids=[1],
                headers={"x-requested-with": "XMLHttpRequest"},
            )
        )
        self.assertFalse(
            should_return_bulk_link_async_response(
                product_ids=list(range(20)),
                headers={},
            )
        )

    def test_build_bulk_link_accepted_payload_uses_action_and_urls(self):
        payload = build_bulk_link_accepted_payload(
            SimpleNamespace(id=99),
            status_url="/status/99/",
            undo_url="/undo/99/",
        )

        self.assertEqual(
            payload,
            {
                "job_id": 99,
                "status_url": "/status/99/",
                "undo_url": "/undo/99/",
            },
        )

    def test_bulk_link_success_message_uses_linked_count(self):
        self.assertEqual(
            bulk_link_success_message(SimpleNamespace(payload_json={"linked": 3})),
            "Linked 3 products.",
        )
        self.assertEqual(
            bulk_link_success_message(SimpleNamespace(payload_json={})),
            "Linked 0 products.",
        )

    def test_is_ajax_request_matches_django_ajax_header(self):
        self.assertTrue(is_ajax_request({"x-requested-with": "XMLHttpRequest"}))
        self.assertFalse(is_ajax_request({}))

    def test_build_undo_link_payload_uses_restored_count(self):
        self.assertEqual(
            build_undo_link_payload(4),
            {"restored": 4, "status": "UNDONE"},
        )

    def test_undo_link_success_message_uses_restored_count(self):
        self.assertEqual(
            undo_link_success_message(4),
            "Undid 4 linked product(s).",
        )

    def test_bulk_link_products_links_and_skips_existing_links(self):
        user = SimpleNamespace(id=1)
        linked_product = SimpleNamespace(
            id=10,
            catalog_perfume_id=None,
            catalog_variant_id=None,
            save=MagicMock(),
        )
        skipped_product = SimpleNamespace(
            id=11,
            catalog_perfume_id=3,
            catalog_variant_id=None,
            save=MagicMock(),
        )
        manager = MagicMock()
        manager.select_for_update.return_value.filter.return_value.order_by.return_value = [
            linked_product,
            skipped_product,
        ]
        supplier_product_model = SimpleNamespace(objects=manager)
        action = SimpleNamespace(id=99, payload_json={})
        link_action_model = SimpleNamespace(
            ACTION_BULK_LINK="bulk_link",
            objects=MagicMock(create=MagicMock(return_value=action)),
        )
        decision_recorder = MagicMock()

        with (
            patch(
                "assistant_linking.services.link_actions.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch("assistant_linking.services.link_actions.prune_link_actions"),
        ):
            returned = bulk_link_products(
                user=user,
                product_ids=[10, 11],
                perfume_id=7,
                variant_id=None,
                allow_overwrite=False,
                apply_to_similar=False,
                reason="operator reason",
                supplier_product_model=supplier_product_model,
                link_action_model=link_action_model,
                decision_recorder=decision_recorder,
            )

        self.assertIs(returned, action)
        self.assertEqual(linked_product.catalog_perfume_id, 7)
        linked_product.save.assert_called_once_with(
            update_fields=["catalog_perfume", "catalog_variant", "updated_at"]
        )
        skipped_product.save.assert_not_called()
        decision_recorder.assert_called_once()
        link_action_model.objects.create.assert_called_once()
        payload = link_action_model.objects.create.call_args.kwargs["payload_json"]
        self.assertEqual(payload["matched"], 2)
        self.assertEqual(payload["linked"], 1)
        self.assertEqual(payload["skipped"], 1)
        self.assertEqual(payload["items"][0]["new_catalog_perfume_id"], 7)
        self.assertTrue(payload["items"][1]["skipped"])

    def test_undo_link_action_restores_linked_products_and_marks_action_undone(self):
        user = SimpleNamespace(id=1)
        product = SimpleNamespace(
            catalog_perfume_id=7,
            catalog_variant_id=8,
            save=MagicMock(),
        )
        manager = MagicMock()
        manager.select_for_update.return_value.get.return_value = product
        supplier_product_model = SimpleNamespace(objects=manager)
        link_action_model = SimpleNamespace(
            ACTION_UNDO_BULK_LINK="undo_bulk_link",
            objects=MagicMock(),
        )
        action = SimpleNamespace(
            id=99,
            payload_json={
                "status": "COMPLETE",
                "items": [
                    {
                        "product_id": 10,
                        "catalog_perfume_id": None,
                        "catalog_variant_id": None,
                        "linked": True,
                    },
                    {"product_id": 11, "linked": False},
                ],
            },
            save=MagicMock(),
        )

        with (
            patch(
                "assistant_linking.services.link_actions.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch("assistant_linking.services.link_actions.prune_link_actions"),
        ):
            restored = undo_link_action(
                action,
                user,
                supplier_product_model=supplier_product_model,
                link_action_model=link_action_model,
                now=lambda: datetime(2026, 5, 1, tzinfo=dt_timezone.utc),
            )

        self.assertEqual(restored, 1)
        self.assertIsNone(product.catalog_perfume_id)
        self.assertIsNone(product.catalog_variant_id)
        product.save.assert_called_once_with(
            update_fields=["catalog_perfume", "catalog_variant", "updated_at"]
        )
        link_action_model.objects.create.assert_called_once_with(
            user=user,
            action_type="undo_bulk_link",
            payload_json={"undone_action_id": 99, "restored": 1},
        )
        self.assertEqual(action.payload_json["status"], "UNDONE")
        self.assertEqual(action.payload_json["undone_at"], "2026-05-01T00:00:00+00:00")
        action.save.assert_called_once_with(update_fields=["payload_json"])

    def test_get_undoable_bulk_link_action_rejects_already_undone_action(self):
        action = SimpleNamespace(payload_json={"status": "UNDONE"})

        with patch(
            "assistant_linking.services.link_actions.get_object_or_404",
            return_value=action,
        ):
            with self.assertRaisesMessage(Exception, "Action already undone."):
                get_undoable_bulk_link_action(
                    action_id=99,
                    user=SimpleNamespace(id=1),
                    link_action_model=FakeLinkActionModel,
                    now=lambda: datetime(2026, 5, 1, tzinfo=dt_timezone.utc),
                )
