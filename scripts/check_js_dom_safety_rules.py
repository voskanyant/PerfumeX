#!/usr/bin/env python
"""Smoke tests for JavaScript DOM safety rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import check_js_dom_safety


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class JavaScriptDomSafetyRuleTests(unittest.TestCase):
    def scan_source(self, source: str) -> list[tuple[int, str]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "example.js"
            path.write_text(source, encoding="utf-8")
            return check_js_dom_safety.scan_file(path)

    def test_text_content_and_dom_nodes_are_allowed(self):
        findings = self.scan_source(
            """
            var row = document.createElement("tr");
            var cell = document.createElement("td");
            cell.textContent = supplierName;
            row.appendChild(document.createTextNode(catalogueName));
            """
        )

        self.assertEqual(findings, [])

    def test_inner_html_is_rejected(self):
        findings = self.scan_source('target.innerHTML = "<p>" + supplierName + "</p>";')

        self.assertEqual(len(findings), 1)
        self.assertIn("innerHTML", findings[0][1])

    def test_outer_html_is_rejected(self):
        findings = self.scan_source('target.outerHTML = "<section></section>";')

        self.assertEqual(len(findings), 1)
        self.assertIn("outerHTML", findings[0][1])

    def test_insert_adjacent_html_is_rejected(self):
        findings = self.scan_source('target.insertAdjacentHTML("beforeend", html);')

        self.assertEqual(len(findings), 1)
        self.assertIn("insertAdjacentHTML", findings[0][1])

    def test_document_write_is_rejected(self):
        findings = self.scan_source('document.write("<script></script>");')

        self.assertEqual(len(findings), 1)
        self.assertIn("document.write", findings[0][1])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(JavaScriptDomSafetyRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
