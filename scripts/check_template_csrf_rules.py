#!/usr/bin/env python
"""Smoke tests for template CSRF checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_template_csrf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateCsrfRuleTests(unittest.TestCase):
    def test_post_form_without_csrf_is_reported(self):
        text = '<form method="post"><button type="submit">Save</button></form>'

        findings = check_template_csrf.csrf_findings(
            check_template_csrf.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)

    def test_post_form_with_csrf_is_allowed(self):
        text = '<form method="post">{% csrf_token %}<button type="submit">Save</button></form>'

        findings = check_template_csrf.csrf_findings(
            check_template_csrf.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_get_form_without_csrf_is_allowed(self):
        text = '<form method="get"><button type="submit">Search</button></form>'

        findings = check_template_csrf.csrf_findings(
            check_template_csrf.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TemplateCsrfRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
