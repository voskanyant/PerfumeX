#!/usr/bin/env python
"""Smoke tests for template URL checker parsing rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import check_template_urls


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateUrlCheckerRuleTests(unittest.TestCase):
    def test_template_files_discovers_html_files_from_configured_dirs(self):
        original_template_dirs = check_template_urls.TEMPLATE_DIRS

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "alpha.html").write_text("", encoding="utf-8")
            (first / "ignored.txt").write_text("", encoding="utf-8")
            (second / "nested").mkdir()
            nested = second / "nested" / "beta.html"
            nested.write_text("", encoding="utf-8")

            try:
                check_template_urls.TEMPLATE_DIRS = (root / "missing", second, first)

                self.assertEqual(
                    check_template_urls.template_files(),
                    [
                        first / "alpha.html",
                        nested,
                    ],
                )
            finally:
                check_template_urls.TEMPLATE_DIRS = original_template_dirs

    def test_url_references_extracts_literal_url_names_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            template = Path(temp_dir) / "sample.html"
            template.write_text(
                """
                {% url 'prices:product_list' %}
                {% url "assistant_core:dashboard" supplier.pk %}
                {% url route_name %}
                """,
                encoding="utf-8",
            )

            self.assertEqual(
                check_template_urls.url_references(template),
                [
                    "prices:product_list",
                    "assistant_core:dashboard",
                ],
            )

    def test_named_url_exists_handles_plain_and_namespaced_routes(self):
        assistant_resolver = SimpleNamespace(
            reverse_dict={"dashboard": object()},
            namespace_dict={},
        )
        root_resolver = SimpleNamespace(
            reverse_dict={"login": object()},
            namespace_dict={"assistant_core": ("", assistant_resolver)},
        )

        self.assertTrue(check_template_urls.named_url_exists(root_resolver, "login"))
        self.assertTrue(
            check_template_urls.named_url_exists(
                root_resolver,
                "assistant_core:dashboard",
            )
        )
        self.assertFalse(
            check_template_urls.named_url_exists(root_resolver, "missing")
        )
        self.assertFalse(
            check_template_urls.named_url_exists(
                root_resolver,
                "assistant_linking:dashboard",
            )
        )
        self.assertFalse(
            check_template_urls.named_url_exists(
                root_resolver,
                "assistant_core:missing",
            )
        )


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        TemplateUrlCheckerRuleTests
    )
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
