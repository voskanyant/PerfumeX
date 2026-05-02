#!/usr/bin/env python
"""Smoke tests for template button checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_template_buttons


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateButtonRuleTests(unittest.TestCase):
    def test_button_without_type_is_reported(self):
        text = '<button class="button">Save</button>'

        findings = check_template_buttons.template_button_findings(
            check_template_buttons.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("explicit type", findings[0].message)

    def test_valid_button_types_are_allowed(self):
        text = """
        <button type="button">Toggle</button>
        <button type="submit">Save</button>
        <button type="reset">Reset</button>
        """

        findings = check_template_buttons.template_button_findings(
            check_template_buttons.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_invalid_literal_button_type_is_reported(self):
        text = '<button type="primary">Save</button>'

        findings = check_template_buttons.template_button_findings(
            check_template_buttons.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("one of", findings[0].message)

    def test_template_driven_button_type_is_allowed(self):
        text = '<button type="{{ button_type }}">Save</button>'

        findings = check_template_buttons.template_button_findings(
            check_template_buttons.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TemplateButtonRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
