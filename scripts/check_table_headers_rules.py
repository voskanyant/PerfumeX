#!/usr/bin/env python
"""Smoke tests for table header checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_table_headers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TableHeaderRuleTests(unittest.TestCase):
    def test_missing_scope_is_reported(self):
        text = """
        <table>
          <thead><tr><th>Name</th></tr></thead>
        </table>
        """

        findings = check_table_headers.table_header_findings(
            check_table_headers.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].message, "header missing scope")

    def test_scoped_headers_are_allowed(self):
        text = """
        <table>
          <thead><tr><th scope="col">Name</th></tr></thead>
        </table>
        """

        findings = check_table_headers.table_header_findings(
            check_table_headers.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_empty_scoped_header_without_name_is_reported(self):
        text = """
        <table>
          <thead><tr><th scope="col"></th></tr></thead>
        </table>
        """

        findings = check_table_headers.table_header_findings(
            check_table_headers.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].message, "empty header missing aria-label or title")

    def test_empty_scoped_header_with_aria_label_is_allowed(self):
        text = """
        <table>
          <thead><tr><th scope="col" aria-label="Actions"></th></tr></thead>
        </table>
        """

        findings = check_table_headers.table_header_findings(
            check_table_headers.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_thead_is_not_treated_as_a_header_cell(self):
        text = "<table><thead><tr><th scope=\"col\">Name</th></tr></thead></table>"

        findings = check_table_headers.table_header_findings(
            check_table_headers.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TableHeaderRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
