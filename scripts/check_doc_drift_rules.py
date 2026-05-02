#!/usr/bin/env python
"""Smoke tests for documentation drift warning rules."""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import check_doc_drift


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class DocDriftRuleTests(unittest.TestCase):
    def run_check(self, paths: set[str]) -> str:
        buffer = io.StringIO()
        with patch.object(check_doc_drift, "changed_paths", return_value=paths):
            with redirect_stdout(buffer):
                result = check_doc_drift.main()
        self.assertEqual(result, 0)
        return buffer.getvalue()

    def run_quiet_check(self, paths: set[str]) -> str:
        buffer = io.StringIO()
        with patch.object(check_doc_drift, "changed_paths", return_value=paths):
            with redirect_stdout(buffer):
                result = check_doc_drift.main(quiet=True)
        self.assertEqual(result, 0)
        return buffer.getvalue()

    def test_model_view_form_service_changes_warn_without_repo_or_domain_docs(self):
        output = self.run_check({"prices/models.py"})

        self.assertIn("Doc drift check: warning-only", output)
        self.assertIn("Python models/views/forms/services/management commands changed", output)

    def test_management_command_changes_warn_without_repo_or_domain_docs(self):
        output = self.run_check({"prices/management/commands/import_emails.py"})

        self.assertIn("Python models/views/forms/services/management commands changed", output)

    def test_ui_changes_warn_without_ui_design_doc(self):
        output = self.run_check({"prices/templates/prices/list.html"})

        self.assertIn("Templates or static UI files changed", output)

    def test_business_changes_warn_without_domain_or_decision_docs(self):
        output = self.run_check({"assistant_linking/services/parser.py"})

        self.assertIn(
            "Business/catalog/import/assistant/alias/linking logic changed", output
        )

    def test_import_command_changes_warn_as_business_logic(self):
        output = self.run_check({"prices/management/commands/import_emails.py"})

        self.assertIn(
            "Business/catalog/import/assistant/alias/linking logic changed", output
        )

    def test_matching_docs_suppress_related_warnings(self):
        output = self.run_check(
            {
                "assistant_linking/services/parser.py",
                "docs/DOMAIN_MODEL.md",
                "docs/REPO_MAP.md",
            }
        )

        self.assertIn("No documentation drift warnings.", output)

    def test_agents_change_accepts_related_focused_doc(self):
        output = self.run_check({"AGENTS.md", "docs/WORKING_RULES.md"})

        self.assertNotIn("AGENTS.md changed without related focused docs", output)

    def test_quiet_mode_keeps_warnings_without_banner(self):
        output = self.run_quiet_check({"prices/models.py"})

        self.assertNotIn("This check compares changed/untracked paths", output)
        self.assertIn("Python models/views/forms/services/management commands changed", output)

    def test_changed_paths_ignore_runtime_and_cache_files(self):
        with patch.object(
            check_doc_drift,
            "git_lines",
            side_effect=[
                ["prices/models.py", "tmp_anna_compare/report.txt"],
                [
                    "prices/__pycache__/models.cpython-313.pyc",
                    "media/imports/file.xlsx",
                ],
            ],
        ):
            paths = check_doc_drift.changed_paths()

        self.assertEqual(paths, {"prices/models.py"})


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DocDriftRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
