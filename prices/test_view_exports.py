from __future__ import annotations

import ast
from pathlib import Path

from django.test import SimpleTestCase

from perfumex import urls as root_urls
from prices import urls, views


BASE_DIR = Path(__file__).resolve().parents[1]
ALLOWED_VIEW_MODULE_HELPERS = {
    "prices/views_imports.py": {
        "_add_action_message",
        "_import_board_redirect",
    },
}


class PricesViewExportTests(SimpleTestCase):
    def parse_views_module(self):
        module_path = BASE_DIR / "prices/views.py"
        return ast.parse(module_path.read_text(encoding="utf-8"))

    def test_views_module_remains_import_only_compatibility_layer(self):
        tree = self.parse_views_module()

        disallowed_nodes = (
            ast.Assign,
            ast.AnnAssign,
            ast.AugAssign,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.FunctionDef,
        )
        offenders = [node for node in tree.body if isinstance(node, disallowed_nodes)]

        self.assertEqual(offenders, [])

    def test_views_module_imports_only_focused_view_modules(self):
        tree = self.parse_views_module()
        import_nodes = [
            node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
        ]

        self.assertTrue(import_nodes)
        for node in import_nodes:
            with self.subTest(lineno=node.lineno):
                self.assertIsInstance(node, ast.ImportFrom)
                self.assertEqual(node.level, 1)
                self.assertTrue(node.module.startswith("views_"))

    def test_focused_view_modules_keep_top_level_helpers_allowlisted(self):
        view_paths = sorted(BASE_DIR.glob("prices/views_*.py"))

        self.assertTrue(view_paths)
        for module_path in view_paths:
            relative_path = module_path.relative_to(BASE_DIR).as_posix()
            tree = ast.parse(module_path.read_text(encoding="utf-8-sig"))
            helper_names = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            }

            with self.subTest(module=relative_path):
                self.assertEqual(
                    helper_names,
                    ALLOWED_VIEW_MODULE_HELPERS.get(relative_path, set()),
                )

    def test_admin_url_view_classes_are_exported_from_views_module(self):
        for pattern in urls.urlpatterns:
            with self.subTest(url_name=pattern.name):
                view_class = pattern.callback.view_class

                self.assertIs(
                    getattr(views, view_class.__name__),
                    view_class,
                )

    def test_root_price_view_classes_are_exported_from_views_module(self):
        for pattern in root_urls.urlpatterns:
            callback = getattr(pattern, "callback", None)
            view_class = getattr(callback, "view_class", None)
            if not view_class or not view_class.__module__.startswith("prices."):
                continue

            with self.subTest(route=str(pattern.pattern)):
                self.assertIs(
                    getattr(views, view_class.__name__),
                    view_class,
                )
