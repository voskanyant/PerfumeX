#!/usr/bin/env python
"""Smoke tests for template label checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_template_labels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateLabelRuleTests(unittest.TestCase):
    def test_label_with_missing_literal_id_is_reported(self):
        text = '<label for="missing">Name</label><input id="present">'

        findings = check_template_labels.template_label_findings(
            check_template_labels.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("missing", findings[0].message)

    def test_label_with_matching_literal_id_is_allowed(self):
        text = '<label for="name">Name</label><input id="name">'

        findings = check_template_labels.template_label_findings(
            check_template_labels.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_django_rendered_field_target_is_allowed(self):
        text = '<label for="id_start_date">Start</label>{{ cbr_range_form.start_date }}'

        findings = check_template_labels.template_label_findings(
            check_template_labels.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_template_expression_target_is_allowed(self):
        text = '<label for="field-{{ row.pk }}">Name</label>'

        findings = check_template_labels.template_label_findings(
            check_template_labels.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_wrapping_label_without_for_is_allowed(self):
        text = '<label><input type="checkbox"> Enabled</label>'

        findings = check_template_labels.template_label_findings(
            check_template_labels.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TemplateLabelRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
