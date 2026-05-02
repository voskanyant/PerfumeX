#!/usr/bin/env python
"""Smoke tests for JavaScript accessibility checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_js_accessibility


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class JavaScriptAccessibilityRuleTests(unittest.TestCase):
    def test_generated_checkbox_without_label_is_reported(self):
        text = """
        var input = document.createElement("input");
        input.type = "checkbox";
        row.appendChild(input);
        """

        findings = check_js_accessibility.js_accessibility_findings(
            check_js_accessibility.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(len(findings), 1)

    def test_generated_checkbox_with_aria_label_is_allowed(self):
        text = """
        var input = document.createElement("input");
        input.type = "checkbox";
        input.setAttribute("aria-label", "Select row");
        row.appendChild(input);
        """

        findings = check_js_accessibility.js_accessibility_findings(
            check_js_accessibility.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(findings, [])

    def test_generated_checkbox_wrapped_by_label_is_allowed(self):
        text = """
        var label = document.createElement("label");
        var input = document.createElement("input");
        input.type = "checkbox";
        label.appendChild(input);
        label.appendChild(document.createTextNode("Enabled"));
        """

        findings = check_js_accessibility.js_accessibility_findings(
            check_js_accessibility.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(findings, [])

    def test_hidden_input_is_ignored(self):
        text = """
        var input = document.createElement("input");
        input.type = "hidden";
        form.appendChild(input);
        """

        findings = check_js_accessibility.js_accessibility_findings(
            check_js_accessibility.BASE_DIR / "example.js",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(JavaScriptAccessibilityRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
