#!/usr/bin/env python
"""Smoke tests for template accessibility checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_template_accessibility


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateAccessibilityRuleTests(unittest.TestCase):
    def test_icon_action_without_labels_is_reported(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<button class="button icon" type="button"></button>',
        )

        self.assertEqual(len(findings), 2)

    def test_icon_action_with_labels_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<a class="button secondary icon" href="/" title="Open" aria-label="Open"></a>',
        )

        self.assertEqual(findings, [])

    def test_plain_svg_icon_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<svg class="icon" aria-hidden="true"></svg>',
        )

        self.assertEqual(findings, [])

    def test_img_without_alt_is_reported(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<img src="logo.png">',
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("alt", findings[0].message)

    def test_decorative_img_with_empty_alt_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<img src="shape.png" alt="">',
        )

        self.assertEqual(findings, [])

    def test_checkbox_without_accessible_label_is_reported(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<input type="checkbox" name="rows">',
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("checkbox/radio", findings[0].message)

    def test_checkbox_with_aria_label_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<input type="checkbox" name="rows" aria-label="Select row">',
        )

        self.assertEqual(findings, [])

    def test_checkbox_with_aria_labelledby_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            (
                '<span id="rows-label">Select row</span>'
                '<input type="checkbox" name="rows" aria-labelledby="rows-label">'
            ),
        )

        self.assertEqual(findings, [])

    def test_radio_with_title_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<input type="radio" name="mode" value="all" title="All rows">',
        )

        self.assertEqual(findings, [])

    def test_checkbox_with_label_for_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<label for="show-inactive">Show inactive</label><input id="show-inactive" type="checkbox">',
        )

        self.assertEqual(findings, [])

    def test_checkbox_wrapped_by_label_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<label><input type="checkbox" name="lock"> Lock parse</label>',
        )

        self.assertEqual(findings, [])

    def test_text_input_without_label_is_reported(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<input type="search" name="q" placeholder="Search">',
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("text/search", findings[0].message)

    def test_text_input_without_type_without_label_is_reported(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<input name="q" placeholder="Search">',
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("text/search", findings[0].message)

    def test_text_input_with_label_for_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<label for="search">Search</label><input id="search" type="search" name="q">',
        )

        self.assertEqual(findings, [])

    def test_text_input_with_aria_label_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<input type="text" name="q" aria-label="Search">',
        )

        self.assertEqual(findings, [])

    def test_text_input_with_aria_labelledby_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            (
                '<span id="search-label">Search</span>'
                '<input type="text" name="q" aria-labelledby="search-label">'
            ),
        )

        self.assertEqual(findings, [])

    def test_text_input_with_title_is_allowed(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<input type="search" name="q" title="Search products">',
        )

        self.assertEqual(findings, [])

    def test_hidden_input_is_allowed_without_label(self):
        findings = check_template_accessibility.template_accessibility_findings(
            check_template_accessibility.BASE_DIR / "example.html",
            '<input type="hidden" name="section" value="aliases">',
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        TemplateAccessibilityRuleTests
    )
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
