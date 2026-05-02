#!/usr/bin/env python
"""Smoke tests for Django template checker discovery rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import check_templates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateCheckerRuleTests(unittest.TestCase):
    def test_template_names_discovers_local_app_templates_in_stable_order(self):
        original_base_dir = check_templates.BASE_DIR
        original_local_apps = check_templates.LOCAL_APPS

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            alpha_templates = root / "alpha" / "templates"
            beta_templates = root / "beta" / "templates"
            alpha_templates.mkdir(parents=True)
            beta_templates.mkdir(parents=True)
            (alpha_templates / "shared.html").write_text("", encoding="utf-8")
            (alpha_templates / "nested").mkdir()
            (alpha_templates / "nested" / "detail.html").write_text(
                "",
                encoding="utf-8",
            )
            (beta_templates / "shared.html").write_text("", encoding="utf-8")
            (beta_templates / "ignored.txt").write_text("", encoding="utf-8")

            try:
                check_templates.BASE_DIR = root
                check_templates.LOCAL_APPS = ("missing_app", "beta", "alpha")

                self.assertEqual(
                    check_templates.template_names(),
                    [
                        "nested/detail.html",
                        "shared.html",
                    ],
                )
            finally:
                check_templates.BASE_DIR = original_base_dir
                check_templates.LOCAL_APPS = original_local_apps


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TemplateCheckerRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
