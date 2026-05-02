#!/usr/bin/env python
"""Smoke tests for JavaScript table-label checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_js_table_labels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class JavaScriptTableLabelRuleTests(unittest.TestCase):
    def test_generated_td_without_label_is_reported(self):
        text = """
        var cell = document.createElement("td");
        cell.textContent = "Value";
        row.appendChild(cell);
        """

        findings = check_js_table_labels.js_table_label_findings(
            check_js_table_labels.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(len(findings), 1)

    def test_generated_td_with_dataset_label_is_allowed(self):
        text = """
        var cell = document.createElement("td");
        cell.dataset.label = "Name";
        cell.textContent = "Value";
        """

        findings = check_js_table_labels.js_table_label_findings(
            check_js_table_labels.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(findings, [])

    def test_generated_td_with_set_attribute_label_is_allowed(self):
        text = """
        var cell = document.createElement("td");
        cell.setAttribute("data-label", "Name");
        cell.textContent = "Value";
        """

        findings = check_js_table_labels.js_table_label_findings(
            check_js_table_labels.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(findings, [])

    def test_generated_td_with_colspan_is_allowed(self):
        text = """
        var cell = document.createElement("td");
        cell.colSpan = 3;
        cell.textContent = "No results.";
        """

        findings = check_js_table_labels.js_table_label_findings(
            check_js_table_labels.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(findings, [])

    def test_local_td_helper_is_checked(self):
        text = """
        var cell = el("td", "muted", "Value");
        row.appendChild(cell);
        """

        findings = check_js_table_labels.js_table_label_findings(
            check_js_table_labels.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(len(findings), 1)


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(JavaScriptTableLabelRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
