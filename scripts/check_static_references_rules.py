#!/usr/bin/env python
"""Smoke tests for static-reference checker parsing rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import check_static_references


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class StaticReferenceCheckerRuleTests(unittest.TestCase):
    def test_template_files_discovers_html_files_from_configured_dirs(self):
        original_template_dirs = check_static_references.TEMPLATE_DIRS

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
                check_static_references.TEMPLATE_DIRS = (
                    root / "missing",
                    second,
                    first,
                )

                self.assertEqual(
                    check_static_references.template_files(),
                    [
                        first / "alpha.html",
                        nested,
                    ],
                )
            finally:
                check_static_references.TEMPLATE_DIRS = original_template_dirs

    def test_static_references_extracts_literal_static_paths_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            template = Path(temp_dir) / "sample.html"
            template.write_text(
                """
                {% static 'prices/css/app.css' %}
                {% static "prices/js/app.js" %}
                {% static dynamic_asset %}
                """,
                encoding="utf-8",
            )

            self.assertEqual(
                check_static_references.static_references(template),
                [
                    "prices/css/app.css",
                    "prices/js/app.js",
                ],
            )


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        StaticReferenceCheckerRuleTests
    )
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
