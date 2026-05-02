from __future__ import annotations

import ast
from pathlib import Path

from django.test import SimpleTestCase

from assistant_linking import urls, views


BASE_DIR = Path(__file__).resolve().parents[2]
ALLOWED_VIEW_MODULE_HELPERS = {
    "assistant_linking/views_normalization.py": {
        "_hidden_product_keywords",
        "_render_normalization_detail",
    },
}


class AssistantLinkingViewExportTests(SimpleTestCase):
    def parse_views_module(self):
        module_path = BASE_DIR / "assistant_linking/views.py"
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
                self.assertIn(
                    True,
                    [
                        node.module == "__future__",
                        node.module.startswith("assistant_linking.views_"),
                    ],
                )

    def test_focused_view_modules_keep_top_level_helpers_allowlisted(self):
        view_paths = sorted(BASE_DIR.glob("assistant_linking/views_*.py"))

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

    def test_url_view_classes_are_exported_from_compatibility_module(self):
        for pattern in urls.urlpatterns:
            with self.subTest(url_name=pattern.name):
                view_class = pattern.callback.view_class

                self.assertIs(
                    getattr(views, view_class.__name__),
                    view_class,
                )
