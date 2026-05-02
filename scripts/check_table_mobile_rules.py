#!/usr/bin/env python
"""Smoke tests for mobile table checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_table_mobile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class MobileTableRuleTests(unittest.TestCase):
    def test_missing_data_label_is_reported(self):
        text = """
        <table class="data-table table-mobile">
          <tbody><tr><td>Missing label</td></tr></tbody>
        </table>
        """

        findings = check_table_mobile.table_mobile_findings(
            check_table_mobile.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)

    def test_data_label_and_colspan_are_allowed(self):
        text = """
        <table class="data-table table-mobile">
          <tbody>
            <tr><td data-label="Name">Value</td></tr>
            <tr><td colspan="2">Empty</td></tr>
          </tbody>
        </table>
        """

        findings = check_table_mobile.table_mobile_findings(
            check_table_mobile.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_non_mobile_tables_are_ignored(self):
        text = '<table class="data-table"><tbody><tr><td>Plain</td></tr></tbody></table>'

        findings = check_table_mobile.table_mobile_findings(
            check_table_mobile.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MobileTableRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
