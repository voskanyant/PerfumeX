#!/usr/bin/env python
"""Smoke tests for template link safety checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_template_links


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateLinkRuleTests(unittest.TestCase):
    def test_javascript_href_is_reported(self):
        findings = check_template_links.template_link_findings(
            check_template_links.BASE_DIR / "example.html",
            '<a href="javascript:history.back()">Cancel</a>',
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("javascript: href", findings[0].message)

    def test_target_blank_without_noopener_is_reported(self):
        findings = check_template_links.template_link_findings(
            check_template_links.BASE_DIR / "example.html",
            '<a href="https://example.com" target="_blank">Open</a>',
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("noopener", findings[0].message)

    def test_target_blank_with_noopener_is_allowed(self):
        findings = check_template_links.template_link_findings(
            check_template_links.BASE_DIR / "example.html",
            '<a href="https://example.com" target="_blank" rel="noreferrer noopener">Open</a>',
        )

        self.assertEqual(findings, [])

    def test_normal_internal_link_is_allowed(self):
        findings = check_template_links.template_link_findings(
            check_template_links.BASE_DIR / "example.html",
            '<a href="{% url \'prices:dashboard\' %}">Dashboard</a>',
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TemplateLinkRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
