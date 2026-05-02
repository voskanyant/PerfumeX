#!/usr/bin/env python
"""Smoke tests for template id checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_template_ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateIdRuleTests(unittest.TestCase):
    def test_duplicate_literal_id_is_reported(self):
        text = '<input id="search"><button id="search" type="button">Clear</button>'

        findings = check_template_ids.template_id_findings(
            check_template_ids.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("duplicate literal id", findings[0].message)

    def test_unique_literal_ids_are_allowed(self):
        text = '<input id="search"><button id="clear-search" type="button">Clear</button>'

        findings = check_template_ids.template_id_findings(
            check_template_ids.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_template_expression_ids_are_allowed(self):
        text = '<tr id="row-{{ object.pk }}"></tr><tr id="row-{{ other.pk }}"></tr>'

        findings = check_template_ids.template_id_findings(
            check_template_ids.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TemplateIdRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
