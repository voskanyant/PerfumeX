#!/usr/bin/env python
"""Smoke tests for template inline-style checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_template_inline_styles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateInlineStyleRuleTests(unittest.TestCase):
    def test_style_attribute_is_reported(self):
        findings = check_template_inline_styles.inline_style_findings(
            check_template_inline_styles.BASE_DIR / "example.html",
            '<div style="margin-top: 1rem"></div>',
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("inline style", findings[0].message)

    def test_style_block_is_reported(self):
        findings = check_template_inline_styles.inline_style_findings(
            check_template_inline_styles.BASE_DIR / "example.html",
            "<style>.card { color: red; }</style>",
        )

        self.assertEqual(len(findings), 2)
        self.assertTrue(all("<style>" in finding.message for finding in findings))

    def test_static_link_is_allowed(self):
        findings = check_template_inline_styles.inline_style_findings(
            check_template_inline_styles.BASE_DIR / "example.html",
            '<link rel="stylesheet" href="{% static \'prices/css/app.css\' %}">',
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        TemplateInlineStyleRuleTests
    )
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
