#!/usr/bin/env python
"""Smoke tests for template layout checker rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import check_template_layout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateLayoutRuleTests(unittest.TestCase):
    def test_partial_templates_are_ignored(self):
        partial_paths = [
            Path("prices/templates/prices/base.html"),
            Path("prices/templates/includes/page_header.html"),
            Path("prices/templates/prices/components/page_header.html"),
            Path("prices/templates/prices/_header_actions.html"),
        ]

        self.assertTrue(
            all(check_template_layout.is_partial_template(path) for path in partial_paths)
        )

    def test_full_page_template_without_base_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "prices" / "templates" / "prices" / "new_page.html"
            template.parent.mkdir(parents=True)
            template.write_text("<h1>Missing shell</h1>", encoding="utf-8")

            original_base_dir = check_template_layout.BASE_DIR
            try:
                check_template_layout.BASE_DIR = root
                failures = check_template_layout.layout_failures([template])
            finally:
                check_template_layout.BASE_DIR = original_base_dir

        self.assertEqual(
            failures,
            [Path("prices/templates/prices/new_page.html")],
        )

    def test_full_page_template_with_base_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "prices" / "templates" / "prices" / "new_page.html"
            template.parent.mkdir(parents=True)
            template.write_text(
                '{% extends "prices/base.html" %}\n{% block content %}{% include "includes/page_header.html" with title="Page" %}{% endblock %}',
                encoding="utf-8",
            )

            original_base_dir = check_template_layout.BASE_DIR
            try:
                check_template_layout.BASE_DIR = root
                failures = check_template_layout.layout_failures([template])
            finally:
                check_template_layout.BASE_DIR = original_base_dir

        self.assertEqual(failures, [])

    def test_full_page_template_with_breadcrumbs_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "prices" / "templates" / "prices" / "detail.html"
            template.parent.mkdir(parents=True)
            template.write_text(
                '{% extends "prices/base.html" %}\n{% block content %}{% include "prices/_breadcrumbs.html" %}<p>Body</p>{% endblock %}',
                encoding="utf-8",
            )

            original_base_dir = check_template_layout.BASE_DIR
            try:
                check_template_layout.BASE_DIR = root
                failures = check_template_layout.layout_failures([template])
            finally:
                check_template_layout.BASE_DIR = original_base_dir

        self.assertEqual(failures, [])

    def test_full_page_template_with_product_header_exception_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "prices" / "templates" / "prices" / "list.html"
            template.parent.mkdir(parents=True)
            template.write_text(
                '{% extends "prices/base.html" %}\n{% block content %}<div class="products-page-header"></div>{% endblock %}',
                encoding="utf-8",
            )

            original_base_dir = check_template_layout.BASE_DIR
            try:
                check_template_layout.BASE_DIR = root
                failures = check_template_layout.layout_failures([template])
            finally:
                check_template_layout.BASE_DIR = original_base_dir

        self.assertEqual(failures, [])

    def test_full_page_template_without_header_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "prices" / "templates" / "prices" / "new_page.html"
            template.parent.mkdir(parents=True)
            template.write_text(
                '{% extends "prices/base.html" %}\n{% block content %}<p>Body</p>{% endblock %}',
                encoding="utf-8",
            )

            original_base_dir = check_template_layout.BASE_DIR
            try:
                check_template_layout.BASE_DIR = root
                failures = check_template_layout.layout_failures([template])
            finally:
                check_template_layout.BASE_DIR = original_base_dir

        self.assertEqual(
            failures,
            [Path("prices/templates/prices/new_page.html")],
        )

    def test_full_page_template_with_login_shell_exception_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "prices" / "templates" / "prices" / "login.html"
            template.parent.mkdir(parents=True)
            template.write_text(
                '{% extends "prices/base.html" %}\n{% block content %}<div class="login-shell"></div>{% endblock %}',
                encoding="utf-8",
            )

            original_base_dir = check_template_layout.BASE_DIR
            try:
                check_template_layout.BASE_DIR = root
                failures = check_template_layout.layout_failures([template])
            finally:
                check_template_layout.BASE_DIR = original_base_dir

        self.assertEqual(failures, [])

    def test_full_page_template_with_supplier_import_hero_exception_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "prices" / "templates" / "prices" / "supplier_import.html"
            template.parent.mkdir(parents=True)
            template.write_text(
                '{% extends "prices/base.html" %}\n{% block content %}<div class="supplier-import-hero"></div>{% endblock %}',
                encoding="utf-8",
            )

            original_base_dir = check_template_layout.BASE_DIR
            try:
                check_template_layout.BASE_DIR = root
                failures = check_template_layout.layout_failures([template])
            finally:
                check_template_layout.BASE_DIR = original_base_dir

        self.assertEqual(failures, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TemplateLayoutRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
