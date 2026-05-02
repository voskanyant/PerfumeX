from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from assistant_core.services.catalog_import_actions import run_catalog_import_action


class FakeCatalogImportForm:
    valid = True
    cleaned_data = {
        "file": "catalogue.xlsx",
        "create_aliases": True,
        "update_existing": False,
    }
    instances = []

    def __init__(self, post_data=None, files=None):
        self.post_data = post_data
        self.files = files
        self.cleaned_data = dict(self.__class__.cleaned_data)
        self.__class__.instances.append(self)

    def is_valid(self):
        return self.valid


class InvalidCatalogImportForm(FakeCatalogImportForm):
    valid = False


class CatalogImportActionServiceTests(SimpleTestCase):
    def tearDown(self):
        FakeCatalogImportForm.instances = []
        InvalidCatalogImportForm.instances = []

    def test_run_catalog_import_action_returns_bound_form_when_invalid(self):
        importer_calls = []

        action = run_catalog_import_action(
            {"post": "data"},
            {"file": object()},
            form_class=InvalidCatalogImportForm,
            importer=lambda *args, **kwargs: importer_calls.append((args, kwargs)),
        )

        self.assertFalse(action.success)
        self.assertEqual(action.message, "Catalogue file was not imported.")
        self.assertIs(action.form, InvalidCatalogImportForm.instances[0])
        self.assertIsNone(action.result)
        self.assertEqual(importer_calls, [])

    def test_run_catalog_import_action_returns_bound_form_on_import_error(self):
        def importer(*args, **kwargs):
            raise ValueError("Bad catalogue file")

        action = run_catalog_import_action(
            {},
            {},
            form_class=FakeCatalogImportForm,
            importer=importer,
        )

        self.assertFalse(action.success)
        self.assertEqual(action.message, "Bad catalogue file")
        self.assertIs(action.form, FakeCatalogImportForm.instances[0])
        self.assertIsNone(action.result)

    def test_run_catalog_import_action_imports_and_resets_form_on_success(self):
        importer_calls = []

        def importer(*args, **kwargs):
            importer_calls.append((args, kwargs))
            return SimpleNamespace(rows_imported=3)

        action = run_catalog_import_action(
            {},
            {},
            form_class=FakeCatalogImportForm,
            importer=importer,
        )

        self.assertTrue(action.success)
        self.assertEqual(action.message, "Imported 3 catalogue rows.")
        self.assertEqual(action.result.rows_imported, 3)
        self.assertIs(action.form, FakeCatalogImportForm.instances[1])
        self.assertEqual(
            importer_calls,
            [
                (
                    ("catalogue.xlsx",),
                    {"create_aliases": True, "update_existing": False},
                )
            ],
        )
