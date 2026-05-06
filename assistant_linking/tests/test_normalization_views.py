from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from assistant_linking.services.normalization_views import (
    PARSED_PRODUCT_HIDDEN_FIELDS,
    SUPPLIER_PRODUCT_HIDDEN_FIELDS,
    attach_unparsed_parse_previews,
    build_bag_queryset,
    build_complete_parsed_id_queryset,
    build_complete_parsed_queryset,
    build_garbage_queryset,
    build_low_confidence_queryset,
    build_missing_brand_queryset,
    build_normalization_dashboard_context,
    build_unparsed_queryset,
    dispatch_parse_unparsed_products,
    dispatch_reparse_stale_products,
    dispatch_reparse_visible_products,
    parse_visible_product_ids,
    refresh_visible_parsed_context,
    refresh_visible_unparsed_context,
)
from prices.services.job_queue import JobDispatchResult


class FakeRecentQuery:
    def __init__(self):
        self.filter_kwargs = None
        self.order_fields = None

    def filter(self, **kwargs):
        self.filter_kwargs = kwargs
        return self

    def order_by(self, *fields):
        self.order_fields = fields
        return self

    def __getitem__(self, item):
        return ["recent-row", item]


class FakeParsedManager:
    def __init__(self):
        self.query = FakeRecentQuery()
        self.select_related_args = None

    def select_related(self, *args):
        self.select_related_args = args
        return self.query


class NormalizationViewServiceTests(SimpleTestCase):
    def test_build_normalization_dashboard_context_hides_recent_ids_key(self):
        manager = FakeParsedManager()
        parsed_model = SimpleNamespace(objects=manager)
        request = SimpleNamespace(GET={})

        context = build_normalization_dashboard_context(
            request,
            hidden_keywords=["tester"],
            stats_builder=lambda _request, _hidden: {
                "recent_ids": [3, 1],
                "total": 12,
                "missing_brand": 2,
            },
            parsed_model=parsed_model,
        )

        self.assertEqual(context["total"], 12)
        self.assertEqual(context["missing_brand"], 2)
        self.assertNotIn("recent_ids", context)
        self.assertTrue(context["hidden_keywords_active"])
        self.assertEqual(context["recent"][0], "recent-row")
        self.assertEqual(
            manager.select_related_args, ("supplier_product", "normalized_brand")
        )
        self.assertEqual(manager.query.filter_kwargs, {"id__in": [3, 1]})
        self.assertEqual(manager.query.order_fields, ("-updated_at",))

    def test_build_unparsed_queryset_applies_search_visibility_and_order(self):
        result = object()
        hidden = MagicMock()
        hidden.order_by.return_value = result
        searched = MagicMock()
        searched.filter.return_value = searched
        base = MagicMock()
        base.filter.return_value = searched
        manager = MagicMock()
        manager.select_related.return_value = base
        supplier_product_model = SimpleNamespace(objects=manager)
        hider_calls = []

        def hider(queryset, hidden_keywords):
            hider_calls.append((queryset, hidden_keywords))
            return hidden

        returned = build_unparsed_queryset(
            "montale",
            ["tester"],
            supplier_product_model=supplier_product_model,
            hider=hider,
        )

        self.assertIs(returned, result)
        manager.select_related.assert_called_once_with("supplier")
        base.filter.assert_called_once_with(assistant_parse__isnull=True)
        searched.filter.assert_called_once()
        self.assertEqual(hider_calls, [(searched.filter.return_value, ["tester"])])
        hidden.order_by.assert_called_once_with("supplier__name", "name")
        self.assertEqual(
            SUPPLIER_PRODUCT_HIDDEN_FIELDS, ("name", "brand", "supplier_sku")
        )

    def test_refresh_visible_unparsed_context_defers_preview_by_default(self):
        product = SimpleNamespace(pk=1)
        context = {"products": [product]}
        parse_saver = MagicMock()
        preview = SimpleNamespace(display_identity="Preview")
        parse_preview_builder = MagicMock(return_value=preview)
        supplier_product_model = SimpleNamespace(objects=MagicMock())

        returned = refresh_visible_unparsed_context(
            context,
            force_refresh=False,
            supplier_product_model=supplier_product_model,
            parse_saver=parse_saver,
            parse_preview_builder=parse_preview_builder,
        )

        self.assertIs(returned, context)
        self.assertTrue(context["allow_refresh_visible"])
        self.assertTrue(context["unparsed_preview_deferred"])
        self.assertEqual(context["products"], [product])
        self.assertFalse(hasattr(product, "parsed_preview"))
        parse_saver.assert_not_called()
        parse_preview_builder.assert_not_called()
        supplier_product_model.objects.filter.assert_not_called()

    def test_refresh_visible_unparsed_context_can_preview_visible_rows(self):
        product = SimpleNamespace(pk=1)
        context = {"products": [product]}
        parse_saver = MagicMock()
        preview = SimpleNamespace(display_identity="Preview")
        parse_preview_builder = MagicMock(return_value=preview)
        supplier_product_model = SimpleNamespace(objects=MagicMock())

        returned = refresh_visible_unparsed_context(
            context,
            force_refresh=False,
            preview=True,
            supplier_product_model=supplier_product_model,
            parse_saver=parse_saver,
            parse_preview_builder=parse_preview_builder,
        )

        self.assertIs(returned, context)
        self.assertTrue(context["allow_refresh_visible"])
        self.assertTrue(context["preview_visible"])
        self.assertEqual(context["products"], [product])
        self.assertIs(product.parsed_preview, preview)
        parse_saver.assert_not_called()
        parse_preview_builder.assert_called_once_with(product)
        supplier_product_model.objects.filter.assert_not_called()

    def test_attach_unparsed_parse_previews_adds_non_persistent_preview(self):
        product = SimpleNamespace(pk=1)
        preview = SimpleNamespace(display_identity="Boucheron / Quatre / 100ml")
        parse_preview_builder = MagicMock(return_value=preview)

        returned = attach_unparsed_parse_previews(
            [product],
            parse_preview_builder=parse_preview_builder,
        )

        self.assertEqual(returned, [product])
        self.assertIs(product.parsed_preview, preview)
        parse_preview_builder.assert_called_once_with(product)

    def test_refresh_visible_unparsed_context_reparses_and_removes_moved_rows(self):
        product_1 = SimpleNamespace(pk=1)
        product_2 = SimpleNamespace(pk=2)
        page_obj = SimpleNamespace(object_list=[product_1, product_2])
        context = {
            "products": [product_1, product_2],
            "object_list": [product_1, product_2],
            "page_obj": page_obj,
        }
        query = MagicMock()
        query.values_list.return_value = [2]
        manager = MagicMock()
        manager.filter.return_value = query
        supplier_product_model = SimpleNamespace(objects=manager)
        parse_saver = MagicMock()
        preview = SimpleNamespace(display_identity="Still unparsed preview")
        parse_preview_builder = MagicMock(return_value=preview)

        returned = refresh_visible_unparsed_context(
            context,
            force_refresh=True,
            supplier_product_model=supplier_product_model,
            parse_saver=parse_saver,
            parse_preview_builder=parse_preview_builder,
        )

        self.assertIs(returned, context)
        self.assertTrue(context["allow_refresh_visible"])
        self.assertEqual(parse_saver.call_count, 2)
        manager.filter.assert_called_once_with(
            pk__in=[1, 2],
            assistant_parse__isnull=True,
        )
        query.values_list.assert_called_once_with("pk", flat=True)
        self.assertEqual(context["products"], [product_2])
        self.assertEqual(context["object_list"], [product_2])
        self.assertEqual(page_obj.object_list, [product_2])
        self.assertIs(product_2.parsed_preview, preview)
        parse_preview_builder.assert_called_once_with(product_2)
        self.assertEqual(context["refreshed_visible_count"], 2)
        self.assertEqual(context["moved_visible_count"], 1)

    def test_dispatch_parse_unparsed_products_runs_management_command(self):
        dispatcher = MagicMock(
            return_value=JobDispatchResult(
                job_id="",
                queue_name="perfumex",
                status="finished",
                queued=False,
                description="Parse unparsed supplier products",
            )
        )

        result = dispatch_parse_unparsed_products(dispatcher=dispatcher)

        self.assertTrue(result.success)
        self.assertEqual(result.message_level, "success")
        dispatcher.assert_called_once_with(
            "reparse_supplier_products",
            only_unparsed=True,
            description="Parse unparsed supplier products",
        )

    def test_dispatch_parse_unparsed_products_reports_queue_failure(self):
        dispatcher = MagicMock(side_effect=RuntimeError("Redis unavailable"))

        result = dispatch_parse_unparsed_products(dispatcher=dispatcher)

        self.assertFalse(result.success)
        self.assertEqual(result.message_level, "error")
        self.assertIn("Redis unavailable", result.message)

    def test_dispatch_reparse_stale_products_runs_stale_refresh(self):
        dispatcher = MagicMock(
            return_value=JobDispatchResult(
                job_id="",
                queue_name="perfumex",
                status="finished",
                queued=False,
                description="Refresh stale normalization parses",
            )
        )

        result = dispatch_reparse_stale_products(dispatcher=dispatcher)

        self.assertTrue(result.success)
        self.assertEqual(result.message_level, "success")
        dispatcher.assert_called_once_with(
            "reparse_supplier_products",
            only_stale=True,
            description="Refresh stale normalization parses",
        )

    def test_dispatch_reparse_stale_products_reports_queue_failure(self):
        dispatcher = MagicMock(side_effect=RuntimeError("Redis unavailable"))

        result = dispatch_reparse_stale_products(dispatcher=dispatcher)

        self.assertFalse(result.success)
        self.assertEqual(result.message_level, "error")
        self.assertIn("Redis unavailable", result.message)

    def test_parse_visible_product_ids_sanitizes_and_limits_values(self):
        result = parse_visible_product_ids(
            ["7", "bad", "7", "-1", "9", "11"],
            limit=2,
        )

        self.assertEqual(result, [7, 9])

    def test_dispatch_reparse_visible_products_runs_exact_id_refresh(self):
        dispatcher = MagicMock(
            return_value=JobDispatchResult(
                job_id="",
                queue_name="perfumex",
                status="finished",
                queued=False,
                description="Refresh 2 visible normalization rows",
            )
        )

        result = dispatch_reparse_visible_products(
            ["4", "6"],
            dispatcher=dispatcher,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.message_level, "success")
        dispatcher.assert_called_once_with(
            "reparse_supplier_products",
            product_ids=[4, 6],
            description="Refresh 2 visible normalization rows",
        )

    def test_dispatch_reparse_visible_products_reports_empty_selection(self):
        dispatcher = MagicMock()

        result = dispatch_reparse_visible_products(["bad"], dispatcher=dispatcher)

        self.assertFalse(result.success)
        self.assertEqual(result.message_level, "warning")
        dispatcher.assert_not_called()

    def test_build_low_confidence_queryset_applies_visibility_filters_and_order(self):
        result = object()
        hidden = MagicMock()
        hidden.exclude.return_value = hidden
        hidden.order_by.return_value = result
        confidence_queryset = MagicMock()
        base = MagicMock()
        base.filter.return_value = confidence_queryset
        manager = MagicMock()
        manager.select_related.return_value = base
        parsed_model = SimpleNamespace(objects=manager)
        hider_calls = []

        def hider(queryset, hidden_keywords):
            hider_calls.append((queryset, hidden_keywords))
            return hidden

        returned = build_low_confidence_queryset(
            "",
            ["tester"],
            parsed_model=parsed_model,
            hider=hider,
        )

        self.assertIs(returned, result)
        manager.select_related.assert_called_once_with(
            "supplier_product",
            "supplier_product__supplier",
            "normalized_brand",
        )
        base.filter.assert_called_once_with(confidence__lt=75)
        self.assertEqual(hider_calls, [(confidence_queryset, ["tester"])])
        hidden.order_by.assert_called_once_with(
            "confidence", "supplier_product__supplier__name", "supplier_product__name"
        )
        self.assertEqual(
            PARSED_PRODUCT_HIDDEN_FIELDS,
            (
                "supplier_product__name",
                "supplier_product__brand",
                "supplier_product__supplier_sku",
            ),
        )

    def test_build_missing_brand_queryset_uses_normal_issue_ordering(self):
        result = object()
        hidden = MagicMock()
        hidden.exclude.return_value = hidden
        hidden.order_by.return_value = result
        missing_brand_queryset = MagicMock()
        searched_queryset = MagicMock()
        missing_brand_queryset.filter.return_value = searched_queryset
        base = MagicMock()
        base.filter.return_value = missing_brand_queryset
        manager = MagicMock()
        manager.select_related.return_value = base
        parsed_model = SimpleNamespace(objects=manager)
        hider_calls = []

        def hider(queryset, hidden_keywords):
            hider_calls.append((queryset, hidden_keywords))
            return hidden

        returned = build_missing_brand_queryset(
            "dior",
            ["tester"],
            parsed_model=parsed_model,
            hider=hider,
        )

        self.assertIs(returned, result)
        base.filter.assert_called_once_with(normalized_brand__isnull=True)
        missing_brand_queryset.filter.assert_called_once()
        self.assertEqual(hider_calls, [(searched_queryset, ["tester"])])
        hidden.order_by.assert_called_once_with("supplier_product__name")

    def test_build_bag_queryset_applies_category_ordering(self):
        result = object()
        hidden = MagicMock()
        hidden.exclude.return_value = hidden
        hidden.filter.return_value = hidden
        hidden.order_by.return_value = result
        base = MagicMock()
        base.filter.return_value = hidden
        manager = MagicMock()
        manager.select_related.return_value = base
        parsed_model = SimpleNamespace(objects=manager)
        hider_calls = []

        def hider(queryset, hidden_keywords):
            hider_calls.append((queryset, hidden_keywords))
            return hidden

        returned = build_bag_queryset(
            "",
            ["tester"],
            parsed_model=parsed_model,
            hider=hider,
        )

        self.assertIs(returned, result)
        base.filter.assert_called_once()
        self.assertEqual(hider_calls, [(base.filter.return_value, ["tester"])])
        hidden.order_by.assert_called_once_with(
            "supplier_product__supplier__name", "supplier_product__name"
        )

    def test_build_garbage_queryset_keeps_garbage_rows_visible(self):
        result = object()
        hidden = MagicMock()
        hidden.order_by.return_value = result
        garbage_queryset = MagicMock()
        garbage_queryset.filter.return_value = garbage_queryset
        base = MagicMock()
        base.filter.return_value = garbage_queryset
        manager = MagicMock()
        manager.select_related.return_value = base
        parsed_model = SimpleNamespace(objects=manager)
        hider_calls = []

        def hider(queryset, hidden_keywords):
            hider_calls.append((queryset, hidden_keywords))
            return hidden

        returned = build_garbage_queryset(
            "sku-1",
            ["tester"],
            parsed_model=parsed_model,
            hider=hider,
        )

        self.assertIs(returned, result)
        base.filter.assert_called_once_with(modifiers__contains=["garbage"])
        garbage_queryset.filter.assert_called_once()
        self.assertEqual(
            hider_calls, [(garbage_queryset.filter.return_value, ["tester"])]
        )
        hidden.order_by.assert_called_once_with(
            "supplier_product__supplier__name", "supplier_product__name"
        )

    def test_build_complete_parsed_queryset_uses_stable_order(self):
        result = object()
        hidden = MagicMock()
        hidden.filter.return_value = hidden
        hidden.exclude.return_value = hidden
        hidden.order_by.return_value = result
        base = MagicMock()
        manager = MagicMock()
        manager.select_related.return_value = base
        parsed_model = SimpleNamespace(objects=manager)
        hider_calls = []

        def hider(queryset, hidden_keywords):
            hider_calls.append((queryset, hidden_keywords))
            return hidden

        returned = build_complete_parsed_queryset(
            "",
            ["tester"],
            parsed_model=parsed_model,
            hider=hider,
        )

        self.assertIs(returned, result)
        self.assertEqual(hider_calls, [(base, ["tester"])])
        hidden.filter.assert_called_once()
        hidden.order_by.assert_called_once_with(
            "-updated_at",
            "supplier_product__supplier__name",
            "supplier_product__name",
        )

    def test_build_complete_parsed_id_queryset_filters_by_ids(self):
        result = object()
        id_queryset = MagicMock()
        id_queryset.exclude.return_value = id_queryset
        id_queryset.filter.return_value = id_queryset
        id_queryset.order_by.return_value = result
        base = MagicMock()
        base.filter.return_value = id_queryset
        manager = MagicMock()
        manager.select_related.return_value = base
        parsed_model = SimpleNamespace(objects=manager)

        returned = build_complete_parsed_id_queryset(
            [5, 7],
            parsed_model=parsed_model,
        )

        self.assertIs(returned, result)
        base.filter.assert_called_once_with(pk__in=[5, 7])
        id_queryset.filter.assert_called_once()
        id_queryset.order_by.assert_called_once_with(
            "-updated_at",
            "supplier_product__supplier__name",
            "supplier_product__name",
        )

    def test_refresh_visible_parsed_context_keeps_rows_when_not_forced(self):
        parsed = SimpleNamespace(pk=1, supplier_product=object())
        context = {"parses": [parsed]}
        parse_saver = MagicMock()
        parsed_id_queryset_builder = MagicMock()

        returned = refresh_visible_parsed_context(
            context,
            force_refresh=False,
            parse_saver=parse_saver,
            parsed_id_queryset_builder=parsed_id_queryset_builder,
        )

        self.assertIs(returned, context)
        self.assertTrue(context["allow_refresh_visible"])
        self.assertNotIn("refreshed_visible", context)
        parse_saver.assert_not_called()
        parsed_id_queryset_builder.assert_not_called()

    def test_refresh_visible_parsed_context_reparses_and_requeries_forced_rows(self):
        product_1 = object()
        product_2 = object()
        parsed_1 = SimpleNamespace(pk=1, supplier_product=product_1)
        parsed_2 = SimpleNamespace(pk=2, supplier_product=product_2)
        refreshed_1 = SimpleNamespace(pk=11)
        refreshed_2 = SimpleNamespace(pk=12)
        page_obj = SimpleNamespace(object_list=[parsed_1, parsed_2])
        context = {"parses": [parsed_1, parsed_2], "page_obj": page_obj}
        parse_saver = MagicMock(side_effect=[refreshed_1, refreshed_2])
        parsed_id_queryset_builder = MagicMock(return_value=["refreshed"])

        returned = refresh_visible_parsed_context(
            context,
            force_refresh=True,
            parse_saver=parse_saver,
            parsed_id_queryset_builder=parsed_id_queryset_builder,
        )

        self.assertIs(returned, context)
        parse_saver.assert_any_call(product_1)
        parse_saver.assert_any_call(product_2)
        parsed_id_queryset_builder.assert_called_once_with([11, 12])
        self.assertEqual(context["parses"], ["refreshed"])
        self.assertEqual(context["object_list"], ["refreshed"])
        self.assertEqual(page_obj.object_list, ["refreshed"])
        self.assertTrue(context["refreshed_visible"])
        self.assertEqual(context["refreshed_visible_count"], 2)
