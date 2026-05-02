#!/usr/bin/env python
"""Smoke tests for JavaScript syntax checker discovery rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import check_js_syntax


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class JavaScriptSyntaxCheckerRuleTests(unittest.TestCase):
    def test_js_files_discovers_static_js_files_in_stable_order(self):
        original_static_dirs = check_js_syntax.STATIC_DIRS

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first_static"
            second = root / "second_static"
            first.mkdir()
            second.mkdir()
            (first / "zeta.js").write_text("const zeta = 1;\n", encoding="utf-8")
            (first / "ignored.css").write_text("", encoding="utf-8")
            (second / "nested").mkdir()
            nested = second / "nested" / "alpha.js"
            nested.write_text("const alpha = 1;\n", encoding="utf-8")

            try:
                check_js_syntax.STATIC_DIRS = (root / "missing", second, first)

                self.assertEqual(
                    check_js_syntax.js_files(),
                    [
                        first / "zeta.js",
                        nested,
                    ],
                )
            finally:
                check_js_syntax.STATIC_DIRS = original_static_dirs


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        JavaScriptSyntaxCheckerRuleTests
    )
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
