from __future__ import annotations

from decimal import Decimal
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from assistant_linking.models import BrandAlias, ParsedSupplierProduct
from assistant_linking.services.normalization_detail import (
    accept_catalog_candidate,
    apply_teaching_to_parsed,
    build_parsed_product_detail_context,
    build_teach_initial,
    get_saved_or_preview_parse,
    lock_supplier_parse,
    manual_decision_snapshot,
    reparse_supplier_product,
    save_brand_alias_for_product,
    save_garbage_keywords_for_product,
    save_product_alias_for_product,
    selected_similar_ids_from_values,
    suggested_catalog_candidate,
    teach_parse_for_product,
)
from assistant_linking.services.normalizer import PARSER_VERSION, save_parse
from catalog.models import Brand
from prices.models import Supplier, SupplierProduct


class FakeTeachingForm:
    SCOPE_GLOBAL = "global"

    def __init__(self, *, initial):
        self.initial = initial


class FakeValidAliasForm:
    saved = False

    def __init__(self, post_data):
        self.post_data = post_data

    def is_valid(self):
        return True

    def save(self):
        type(self).saved = True


class FakeInvalidAliasForm:
    def __init__(self, post_data):
        self.post_data = post_data
        self.errors = {"alias_text": ["Required"]}
        self.fields = {"alias_text": SimpleNamespace(widget=SimpleNamespace(attrs={}))}

    def is_valid(self):
        return False

    def save(self):
        raise AssertionError("Invalid form should not be saved")


class FakeInvalidTeachForm(FakeInvalidAliasForm):
    pass


class FakeValidTeachForm:
    SCOPE_SUPPLIER = "supplier"

    def __init__(self, post_data):
        self.post_data = post_data
        self.cleaned_data = {
            "brand_name": "Montale",
            "product_name": "Vanilla Extasy",
            "alias_scope": "supplier",
            "supplier_brand_text": "montale",
            "supplier_product_text": "vanilla extasy",
            "product_excluded_terms": "tester",
            "concentration": "Eau de Parfum",
            "audience": "",
            "supplier_concentration_text": "edp",
            "supplier_size_text": "100ml",
            "size_ml": Decimal("100.00"),
            "packaging": " Box ",
            "variant_type": "Tester",
            "lock_parse": True,
            "reparse_similar": False,
        }

    def is_valid(self):
        return True


class FakeSuggestionModel:
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    objects = MagicMock()


class NormalizationDetailServiceTests(SimpleTestCase):
    def test_suggested_catalog_candidate_requires_strong_concentration_conflict(self):
        weak = SimpleNamespace(score=79, conflicts=["concentration differs"])
        strong = SimpleNamespace(score=95, conflicts=["concentration differs"])

        self.assertIsNone(suggested_catalog_candidate(None, [weak]))
        self.assertIs(suggested_catalog_candidate(None, [strong]), strong)
        self.assertIsNone(suggested_catalog_candidate(object(), [strong]))
        self.assertIsNone(
            suggested_catalog_candidate(
                None, [SimpleNamespace(score=95, conflicts=["brand differs"])]
            )
        )

    def test_manual_decision_snapshot_serializes_audit_fields(self):
        decision = SimpleNamespace(
            id=1,
            supplier_product_id=2,
            perfume_id=3,
            variant_id=None,
            decision_type="approve_perfume",
            reason="Accepted from normalization catalogue candidates.",
            apply_to_similar=False,
            created_by_id=4,
            created_at=SimpleNamespace(isoformat=lambda: "2026-05-01T10:00:00"),
        )

        self.assertEqual(
            manual_decision_snapshot(decision),
            {
                "id": 1,
                "supplier_product_id": 2,
                "perfume_id": 3,
                "variant_id": None,
                "decision_type": "approve_perfume",
                "reason": "Accepted from normalization catalogue candidates.",
                "apply_to_similar": False,
                "created_by_id": 4,
                "created_at": "2026-05-01T10:00:00",
            },
        )

    def test_build_teach_initial_prefills_from_catalog_link(self):
        brand = SimpleNamespace(name="12 Parfumeurs")
        perfume = SimpleNamespace(
            brand=brand,
            name="Malmaison",
            concentration="Extrait de Parfum",
            audience="unisex",
        )
        variant = SimpleNamespace(
            size_ml=Decimal("100.00"),
            variant_type="standard",
            packaging="box",
        )
        parsed = SimpleNamespace(
            normalized_brand_id=None,
            normalized_brand=None,
            detected_brand_text="12 parfumeurs",
            product_name_text="malmaison",
            concentration="Eau de Parfum",
            raw_size_text="100ml",
            size_ml=Decimal("90.00"),
            supplier_gender_hint="",
            variant_type="tester",
            packaging="",
        )
        product = SimpleNamespace(size="100ml")

        initial = build_teach_initial(
            product=product,
            parsed=parsed,
            teaching_perfume=perfume,
            teaching_variant=variant,
            brand_alias_text="12 parfumeurs",
            product_alias_text="malmaison",
            existing_blockers="tester",
            teaching_form_class=FakeTeachingForm,
        )

        self.assertEqual(initial["brand_name"], "12 Parfumeurs")
        self.assertEqual(initial["product_name"], "Malmaison")
        self.assertEqual(initial["concentration"], "Extrait de Parfum")
        self.assertEqual(initial["size_ml"], "100")
        self.assertEqual(initial["variant_type"], "standard")
        self.assertEqual(initial["packaging"], "box")
        self.assertEqual(initial["product_excluded_terms"], "tester")
        self.assertEqual(initial["alias_scope"], "global")

    def test_build_parsed_product_detail_context_uses_injected_collaborators(self):
        brand = SimpleNamespace(name="Montale")
        perfume = SimpleNamespace(
            brand=brand,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
            audience="",
        )
        variant = SimpleNamespace(
            size_ml=Decimal("100.00"),
            variant_type="standard",
            packaging="",
        )
        candidate = SimpleNamespace(
            score=95,
            conflicts=["concentration differs"],
            perfume=perfume,
            variant=variant,
        )
        supplier = SimpleNamespace(id=1)
        product = SimpleNamespace(
            supplier=supplier,
            name="montale vanilla extasy edp100ml",
            brand="montale",
            size="100ml",
            catalog_perfume=None,
            catalog_variant=None,
        )
        parsed = SimpleNamespace(
            modifiers=[],
            is_set=False,
            normalized_brand_id=None,
            normalized_brand=None,
            detected_brand_text="montale",
            product_name_text="vanilla extasy",
            concentration="Eau de Parfum",
            raw_size_text="100ml",
            size_ml=Decimal("100.00"),
            supplier_gender_hint="",
            variant_type="",
            packaging="",
        )
        calls = {}

        def similar_rows_builder(given_product, given_parsed, *, hidden_terms):
            calls["similar"] = (given_product, given_parsed, hidden_terms)
            return ["similar"]

        def rule_impact_builder(
            given_product,
            brand_alias_text,
            product_alias_text,
            existing_blockers,
            *,
            hidden_terms,
        ):
            calls["rule_impact"] = (
                given_product,
                brand_alias_text,
                product_alias_text,
                existing_blockers,
                hidden_terms,
            )
            return {"count": 1}

        context = build_parsed_product_detail_context(
            product=product,
            hidden_keywords=["tester"],
            context_overrides={},
            parse_saver=lambda _product: parsed,
            candidate_builder=lambda _parsed: [candidate],
            similar_rows_builder=similar_rows_builder,
            rule_impact_builder=rule_impact_builder,
            alias_finder=lambda _parsed, _product: SimpleNamespace(
                excluded_terms="intense"
            ),
            teaching_form_class=FakeTeachingForm,
            catalog_reference_builder=lambda: {"catalog_brands": ["brand"]},
        )

        self.assertIs(context["parsed"], parsed)
        self.assertEqual(context["catalog_candidates"], [candidate])
        self.assertIs(context["suggested_catalog_candidate"], candidate)
        self.assertEqual(context["similar_rows"], ["similar"])
        self.assertEqual(context["rule_impact"], {"count": 1})
        self.assertEqual(context["catalog_brands"], ["brand"])
        self.assertEqual(context["teach_form"].initial["brand_name"], "Montale")
        self.assertEqual(
            context["teach_form"].initial["product_name"], "Vanilla Extasy"
        )
        self.assertEqual(
            context["teach_form"].initial["product_excluded_terms"], "intense"
        )
        self.assertEqual(calls["similar"], (product, parsed, ["tester"]))
        self.assertEqual(
            calls["rule_impact"],
            (
                product,
                "montale",
                "vanilla extasy",
                "intense",
                ["tester"],
            ),
        )

    def test_get_saved_or_preview_parse_prefers_existing_saved_parse(self):
        existing = SimpleNamespace(id=7)
        product = SimpleNamespace(assistant_parse=existing)
        preview_builder = MagicMock()

        returned = get_saved_or_preview_parse(
            product,
            parse_preview_builder=preview_builder,
        )

        self.assertIs(returned, existing)
        preview_builder.assert_not_called()

    def test_accept_catalog_candidate_updates_parse_link_aliases_and_suggestion(self):
        supplier = SimpleNamespace(id=11)
        user = SimpleNamespace(id=22)
        brand = SimpleNamespace(name="Montale")
        perfume = SimpleNamespace(
            id=3,
            brand=brand,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
            audience="",
        )
        variant = SimpleNamespace(
            id=4,
            size_ml=Decimal("100.00"),
            packaging="box",
            variant_type="standard",
            is_tester=False,
        )
        product = SimpleNamespace(
            supplier=supplier,
            brand="montale",
            catalog_perfume_id=None,
            catalog_variant_id=None,
            catalog_perfume=None,
            catalog_variant=None,
            save=MagicMock(),
        )
        parsed = SimpleNamespace(
            detected_brand_text="montale",
            product_name_text="vanilla extasy",
            save=MagicMock(),
        )
        suggestion = SimpleNamespace(
            status=FakeSuggestionModel.STATUS_PENDING,
            reviewed_by=None,
            reviewed_at=None,
            save=MagicMock(),
        )
        suggestion_query = MagicMock()
        suggestion_query.select_for_update.return_value.filter.return_value.order_by.return_value.first.return_value = (
            suggestion
        )
        FakeSuggestionModel.objects = suggestion_query
        perfume_model = SimpleNamespace(
            objects=MagicMock(select_related=MagicMock(return_value="perfume-query"))
        )
        supplier_product_model = SimpleNamespace(objects=MagicMock())
        supplier_product_model.objects.select_for_update.return_value.select_related.return_value = (
            "product-query"
        )
        brand_alias_model = SimpleNamespace(objects=MagicMock())
        product_alias_model = SimpleNamespace(objects=MagicMock())
        decision_recorder = MagicMock()

        with (
            patch(
                "assistant_linking.services.normalization_detail.get_object_or_404",
                side_effect=[perfume, variant, product],
            ),
            patch(
                "assistant_linking.services.normalization_detail.transaction.atomic",
                return_value=nullcontext(),
            ),
        ):
            result = accept_catalog_candidate(
                supplier_product_id=99,
                perfume_id=3,
                variant_id=4,
                alias_scope="supplier",
                excluded_terms="tester",
                user=user,
                perfume_model=perfume_model,
                variant_model=object,
                supplier_product_model=supplier_product_model,
                suggestion_model=FakeSuggestionModel,
                brand_alias_model=brand_alias_model,
                product_alias_model=product_alias_model,
                parse_saver=lambda _product: parsed,
                decision_recorder=decision_recorder,
            )

        self.assertTrue(result.accepted)
        self.assertEqual(result.message_level, "success")
        brand_alias_model.objects.update_or_create.assert_called_once()
        product_alias_model.objects.update_or_create.assert_called_once()
        self.assertEqual(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Vanilla Extasy")
        self.assertEqual(parsed.size_ml, Decimal("100.00"))
        self.assertTrue(parsed.locked_by_human)
        parsed.save.assert_called_once()
        self.assertIs(product.catalog_perfume, perfume)
        self.assertIs(product.catalog_variant, variant)
        product.save.assert_called_once_with(
            update_fields=["catalog_perfume", "catalog_variant", "updated_at"]
        )
        decision_recorder.assert_called_once()
        self.assertEqual(suggestion.status, FakeSuggestionModel.STATUS_APPROVED)
        self.assertIs(suggestion.reviewed_by, user)
        suggestion.save.assert_called_once_with(
            update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
        )

    def test_reparse_supplier_product_calls_parser_with_force_flag(self):
        product = SimpleNamespace(id=9)
        parse_saver = MagicMock()

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=product,
        ) as getter:
            result = reparse_supplier_product(
                supplier_product_id=9,
                force=True,
                supplier_product_model=object,
                parse_saver=parse_saver,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Product parsed.")
        getter.assert_called_once_with(object, pk=9)
        parse_saver.assert_called_once_with(product, force=True)

    def test_save_garbage_keywords_for_product_rejects_empty_keywords(self):
        product = SimpleNamespace(id=9)
        manager = MagicMock()
        manager.select_related.return_value = "product-query"
        supplier_product_model = SimpleNamespace(objects=manager)
        global_rule_model = SimpleNamespace(objects=MagicMock())
        cache_clearer = MagicMock()
        parse_saver = MagicMock()

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=product,
        ):
            result = save_garbage_keywords_for_product(
                supplier_product_id=9,
                keywords_text="",
                user=SimpleNamespace(id=1),
                supplier_product_model=supplier_product_model,
                global_rule_model=global_rule_model,
                keyword_normalizer=lambda _text: "",
                cache_clearer=cache_clearer,
                parse_saver=parse_saver,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.message_level, "error")
        global_rule_model.objects.update_or_create.assert_not_called()
        cache_clearer.assert_not_called()
        parse_saver.assert_not_called()

    def test_save_garbage_keywords_for_product_creates_rules_and_reparses(self):
        product = SimpleNamespace(id=9)
        user = SimpleNamespace(id=1)
        manager = MagicMock()
        manager.select_related.return_value = "product-query"
        supplier_product_model = SimpleNamespace(objects=manager)
        global_rule_model = SimpleNamespace(objects=MagicMock())
        cache_clearer = MagicMock()
        parse_saver = MagicMock()

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=product,
        ):
            result = save_garbage_keywords_for_product(
                supplier_product_id=9,
                keywords_text="gift set\nmini",
                user=user,
                supplier_product_model=supplier_product_model,
                global_rule_model=global_rule_model,
                keyword_normalizer=lambda _text: "gift set\nmini",
                cache_clearer=cache_clearer,
                parse_saver=parse_saver,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.message_level, "success")
        self.assertEqual(global_rule_model.objects.update_or_create.call_count, 2)
        global_rule_model.objects.update_or_create.assert_any_call(
            rule_kind="garbage_keyword",
            scope_type="global",
            rule_text="gift set",
            defaults={
                "title": "Garbage keyword: gift set",
                "scope_value": "",
                "priority": 10,
                "confidence": 100,
                "active": True,
                "approved": True,
                "created_by": user,
            },
        )
        cache_clearer.assert_called_once_with()
        parse_saver.assert_called_once_with(product, force=True)

    def test_lock_supplier_parse_marks_parse_locked(self):
        parsed = SimpleNamespace(locked_by_human=False, save=MagicMock())

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=parsed,
        ) as getter:
            result = lock_supplier_parse(
                supplier_product_id=9,
                parsed_model=object,
            )

        self.assertTrue(result.success)
        self.assertTrue(parsed.locked_by_human)
        getter.assert_called_once_with(object, supplier_product_id=9)
        parsed.save.assert_called_once_with(
            update_fields=["locked_by_human", "updated_at"]
        )

    def test_save_brand_alias_for_product_saves_valid_form(self):
        FakeValidAliasForm.saved = False
        product = SimpleNamespace(id=9)
        manager = MagicMock()
        manager.select_related.return_value = "product-query"
        supplier_product_model = SimpleNamespace(objects=manager)

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=product,
        ) as getter:
            result = save_brand_alias_for_product(
                supplier_product_id=9,
                post_data={"alias_text": "montale"},
                supplier_product_model=supplier_product_model,
                form_class=FakeValidAliasForm,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Brand alias saved.")
        self.assertIs(result.product, product)
        self.assertTrue(FakeValidAliasForm.saved)
        getter.assert_called_once_with("product-query", pk=9)

    def test_save_brand_alias_for_product_returns_invalid_form_for_detail(self):
        product = SimpleNamespace(id=9)
        manager = MagicMock()
        manager.select_related.return_value = "product-query"
        supplier_product_model = SimpleNamespace(objects=manager)

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=product,
        ):
            result = save_brand_alias_for_product(
                supplier_product_id=9,
                post_data={"alias_text": ""},
                supplier_product_model=supplier_product_model,
                form_class=FakeInvalidAliasForm,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.message_level, "error")
        self.assertEqual(result.form_context_key, "brand_alias_form")
        self.assertEqual(
            result.form.fields["alias_text"].widget.attrs,
            {
                "aria-describedby": "id_alias_text_errors",
                "aria-invalid": "true",
            },
        )

    def test_save_product_alias_for_product_returns_product_alias_context_key(self):
        product = SimpleNamespace(id=9)
        manager = MagicMock()
        manager.select_related.return_value = "product-query"
        supplier_product_model = SimpleNamespace(objects=manager)

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=product,
        ):
            result = save_product_alias_for_product(
                supplier_product_id=9,
                post_data={"alias_text": ""},
                supplier_product_model=supplier_product_model,
                form_class=FakeInvalidAliasForm,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.message, "Product alias was not saved.")
        self.assertEqual(result.form_context_key, "product_alias_form")

    def test_selected_similar_ids_from_values_keeps_only_numeric_values(self):
        self.assertEqual(
            selected_similar_ids_from_values(["1", "abc", "02", "", "3.5"]),
            {1, 2},
        )

    def test_apply_teaching_to_parsed_updates_identity_and_flags(self):
        brand = SimpleNamespace(name="Montale")
        parsed = SimpleNamespace(save=MagicMock())

        apply_teaching_to_parsed(
            parsed=parsed,
            brand=brand,
            brand_alias_text="montale",
            product_name="Vanilla Extasy",
            data={
                "concentration": "Eau de Parfum",
                "size_ml": Decimal("100.00"),
                "supplier_size_text": "100ml",
                "audience": "",
                "packaging": " Box Tester ",
                "variant_type": "Tester",
                "lock_parse": True,
            },
        )

        self.assertIs(parsed.normalized_brand, brand)
        self.assertEqual(parsed.detected_brand_text, "montale")
        self.assertEqual(parsed.product_name_text, "Vanilla Extasy")
        self.assertEqual(parsed.concentration, "Eau de Parfum")
        self.assertEqual(parsed.packaging, "box tester")
        self.assertEqual(parsed.variant_type, "tester")
        self.assertTrue(parsed.is_tester)
        self.assertTrue(parsed.locked_by_human)
        parsed.save.assert_called_once()

    def test_teach_parse_for_product_returns_invalid_form_for_detail(self):
        product = SimpleNamespace(id=9)
        manager = MagicMock()
        manager.select_related.return_value = "product-query"
        supplier_product_model = SimpleNamespace(objects=manager)
        parse_saver = MagicMock()

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=product,
        ):
            result = teach_parse_for_product(
                supplier_product_id=9,
                post_data={"brand_name": ""},
                supplier_product_model=supplier_product_model,
                form_class=FakeInvalidTeachForm,
                parse_saver=parse_saver,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.message, "Teaching form has invalid values.")
        self.assertEqual(result.form_context_key, "teach_form")
        self.assertEqual(
            result.form.fields["alias_text"].widget.attrs,
            {
                "aria-describedby": "id_alias_text_errors",
                "aria-invalid": "true",
            },
        )
        parse_saver.assert_called_once_with(product)

    def test_teach_parse_for_product_saves_aliases_and_parsed_row(self):
        supplier = SimpleNamespace(id=7)
        product = SimpleNamespace(id=9, pk=9, supplier=supplier)
        parsed = SimpleNamespace(save=MagicMock())
        manager = MagicMock()
        manager.select_related.return_value = "product-query"
        supplier_product_model = SimpleNamespace(objects=manager)
        brand = SimpleNamespace(name="Montale")
        brand_manager = MagicMock()
        brand_manager.filter.return_value.first.return_value = brand
        brand_model = SimpleNamespace(objects=brand_manager)
        brand_alias_model = SimpleNamespace(objects=MagicMock())
        product_alias_model = SimpleNamespace(objects=MagicMock())

        with patch(
            "assistant_linking.services.normalization_detail.get_object_or_404",
            return_value=product,
        ):
            result = teach_parse_for_product(
                supplier_product_id=9,
                post_data={"brand_name": "Montale"},
                selected_similar_values=[],
                supplier_product_model=supplier_product_model,
                brand_model=brand_model,
                brand_alias_model=brand_alias_model,
                product_alias_model=product_alias_model,
                form_class=FakeValidTeachForm,
                parse_saver=lambda _product: parsed,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.updated_similar, 0)
        self.assertIn("Montale / Vanilla Extasy", result.message)
        brand_alias_model.objects.get_or_create.assert_called_once()
        product_alias_model.objects.update_or_create.assert_called_once()
        self.assertIs(parsed.normalized_brand, brand)
        self.assertEqual(parsed.product_name_text, "Vanilla Extasy")
        self.assertEqual(parsed.variant_type, "tester")
        parsed.save.assert_called_once()


class NormalizationDetailDatabaseTests(TestCase):
    def test_detail_context_does_not_save_parse_for_unparsed_product(self):
        supplier = Supplier.objects.create(name="Supplier", code="supplier")
        product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="armani-vert-malachite",
            name="Armani VERT MALACHITE (U) 100ml EDP TECTEP",
        )

        context = build_parsed_product_detail_context(
            product=product,
            hidden_keywords=[],
            candidate_builder=lambda _parsed: [],
            similar_rows_builder=lambda *_args, **_kwargs: [],
            rule_impact_builder=lambda *_args, **_kwargs: {"count": 0, "risky": 0},
            alias_finder=lambda _parsed, _product: None,
            teaching_form_class=FakeTeachingForm,
            catalog_reference_builder=lambda: {},
        )

        self.assertFalse(context["parsed_is_saved"])
        self.assertFalse(
            ParsedSupplierProduct.objects.filter(supplier_product=product).exists()
        )

    def test_detail_context_refreshes_stale_saved_parse(self):
        supplier = Supplier.objects.create(name="Supplier", code="supplier")
        brand = Brand.objects.create(name="100 Bon")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="100 Bon",
            normalized_alias="100 bon",
        )
        product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="100-bon-ru-stale",
            name="100 Bon Bois De Mangrove парфюмированная вода тестер 50 мл. уни",
        )
        parsed = save_parse(product)
        parsed.product_name_text = "100 bon bois de mangrove парфюмированная вода тестер уни"
        parsed.concentration = ""
        parsed.supplier_gender_hint = ""
        parsed.warnings = ["brand missing", "concentration missing", "gender missing"]
        parsed.parser_version = "deterministic-v1"
        parsed.save()

        refreshed = get_saved_or_preview_parse(product)

        self.assertIsInstance(refreshed, ParsedSupplierProduct)
        self.assertEqual(refreshed.parser_version, PARSER_VERSION)
        self.assertEqual(refreshed.normalized_brand, brand)
        self.assertEqual(refreshed.product_name_text, "bois de mangrove")
        self.assertEqual(refreshed.concentration, "Eau de Parfum")
        self.assertEqual(refreshed.supplier_gender_hint, "Unisex")
        self.assertTrue(refreshed.is_tester)
        self.assertEqual(refreshed.warnings, [])

    def test_detail_context_preserves_locked_stale_parse(self):
        supplier = Supplier.objects.create(name="Supplier", code="supplier")
        product = SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="locked-stale",
            name="Unknown Scent 50ml EDP",
        )
        parsed = ParsedSupplierProduct.objects.create(
            supplier_product=product,
            raw_name=product.name,
            normalized_text="unknown scent 50ml edp",
            product_name_text="locked name",
            parser_version="deterministic-v1",
            locked_by_human=True,
        )

        result = get_saved_or_preview_parse(product)

        self.assertEqual(result.pk, parsed.pk)
        self.assertEqual(result.product_name_text, "locked name")
        self.assertEqual(result.parser_version, "deterministic-v1")
