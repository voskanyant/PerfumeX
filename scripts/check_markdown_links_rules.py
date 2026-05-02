#!/usr/bin/env python
"""Smoke tests for Markdown local-link checking rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_markdown_links


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class MarkdownLinkRuleTests(unittest.TestCase):
    def run_check(self, source: str, files: dict[str, str] | None = None) -> list[tuple[int, str]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            doc_path = base_dir / "docs" / "example.md"
            doc_path.parent.mkdir(parents=True)
            doc_path.write_text(source, encoding="utf-8")
            for relative_path, content in (files or {}).items():
                path = base_dir / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            with patch.object(check_markdown_links, "BASE_DIR", base_dir):
                return check_markdown_links.check_file(doc_path)

    def test_existing_relative_link_passes(self):
        findings = self.run_check(
            "[Read me](../README.md)",
            {"README.md": "# Read me"},
        )

        self.assertEqual(findings, [])

    def test_missing_relative_link_fails(self):
        findings = self.run_check("[Missing](missing.md)")

        self.assertEqual(findings, [(1, "missing.md")])

    def test_anchor_only_link_is_ignored(self):
        findings = self.run_check("[Section](#section)")

        self.assertEqual(findings, [])

    def test_external_link_is_ignored(self):
        findings = self.run_check("[Django](https://www.djangoproject.com/)")

        self.assertEqual(findings, [])

    def test_existing_link_with_title_passes(self):
        findings = self.run_check(
            '[Read me](../README.md "Human docs")',
            {"README.md": "# Read me"},
        )

        self.assertEqual(findings, [])

    def test_angle_wrapped_path_with_spaces_passes(self):
        findings = self.run_check(
            "[Report](<../My Report.md>)",
            {"My Report.md": "# Report"},
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MarkdownLinkRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
