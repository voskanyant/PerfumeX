#!/usr/bin/env python
"""Smoke tests for agent documentation shape rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_agent_docs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


def agents_doc(extra_text: str = "") -> str:
    required_lines = "\n".join(check_agent_docs.AGENTS_REQUIRED_TEXT)
    focused_links = "\n".join(check_agent_docs.FOCUSED_DOCS)
    command_lines = "\n".join(
        f"python {script_path}"
        for script_path in check_agent_docs.expected_agent_command_scripts()
    )
    return (
        "# AGENTS.md\n\n"
        "## Purpose of this document\n\n"
        "Agent entry point.\n\n"
        f"{required_lines}\n"
        f"{focused_links}\n"
        f"{command_lines}\n"
        f"{extra_text}\n"
    )


def focused_doc() -> str:
    return (
        "# Focused Doc\n\n"
        "## Purpose of this document\n\n"
        "Focused repo memory.\n\n"
        "Related docs: [AGENTS.md](../AGENTS.md).\n"
    )


def drift_checklist_doc() -> str:
    target_lines = "\n".join(
        f"make {target}" for target in check_agent_docs.expected_make_targets()
    )
    return (
        "# Drift Checklist\n\n"
        "## Purpose of this document\n\n"
        "Focused repo memory.\n\n"
        "Related docs: [AGENTS.md](../AGENTS.md).\n\n"
        f"{target_lines}\n"
    )


def contributing_doc() -> str:
    target_lines = "\n".join(
        f"make {target}" for target in check_agent_docs.expected_make_targets()
    )
    return (
        "# Contributing\n\n"
        "## Purpose of this document\n\n"
        "Short checklist.\n\n"
        f"{target_lines}\n"
    )


class AgentDocsRuleTests(unittest.TestCase):
    def build_docs(self, omitted: set[str] | None = None, overrides: dict[str, str] | None = None) -> Path:
        base_dir = Path(tempfile.mkdtemp())
        omitted = omitted or set()
        overrides = overrides or {}
        for relative_path in check_agent_docs.REQUIRED_DOCS:
            if relative_path in omitted:
                continue
            path = base_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative_path == "AGENTS.md":
                content = agents_doc()
            elif relative_path == "README.md":
                content = "# README\n"
            elif relative_path == "CONTRIBUTING.md":
                content = contributing_doc()
            elif relative_path == "docs/DRIFT_CHECKLIST.md":
                content = drift_checklist_doc()
            else:
                content = focused_doc()
            path.write_text(overrides.get(relative_path, content), encoding="utf-8")
        return base_dir

    def test_valid_doc_set_passes_all_checks(self):
        base_dir = self.build_docs()
        with patch.object(check_agent_docs, "BASE_DIR", base_dir):
            self.assertEqual(check_agent_docs.check_required_files(), [])
            self.assertEqual(check_agent_docs.check_purpose_sections(), [])
            self.assertEqual(check_agent_docs.check_agents_protocol(), [])
            self.assertEqual(check_agent_docs.check_doc_links(), [])
            self.assertEqual(check_agent_docs.check_agents_commands(), [])
            self.assertEqual(check_agent_docs.check_drift_checklist_targets(), [])
            self.assertEqual(check_agent_docs.check_contributing_targets(), [])

    def test_missing_required_doc_is_reported(self):
        base_dir = self.build_docs(omitted={"docs/DECISIONS.md"})
        with patch.object(check_agent_docs, "BASE_DIR", base_dir):
            failures = check_agent_docs.check_required_files()

        self.assertIn("missing required doc: docs/DECISIONS.md", failures)

    def test_missing_purpose_section_is_reported(self):
        base_dir = self.build_docs(
            overrides={"docs/REPO_MAP.md": "# Repository Map\n\nRelated docs: AGENTS.md\n"}
        )
        with patch.object(check_agent_docs, "BASE_DIR", base_dir):
            failures = check_agent_docs.check_purpose_sections()

        self.assertIn("missing purpose section: docs/REPO_MAP.md", failures)

    def test_missing_agents_protocol_text_is_reported(self):
        base_dir = self.build_docs(
            overrides={"AGENTS.md": agents_doc().replace("Code changed", "")}
        )
        with patch.object(check_agent_docs, "BASE_DIR", base_dir):
            failures = check_agent_docs.check_agents_protocol()

        self.assertIn("AGENTS.md missing required protocol text: Code changed", failures)

    def test_focused_doc_missing_agents_backlink_is_reported(self):
        base_dir = self.build_docs(
            overrides={
                "docs/DOMAIN_MODEL.md": (
                    "# Domain Model\n\n"
                    "## Purpose of this document\n\n"
                    "No backlink.\n"
                )
            }
        )
        with patch.object(check_agent_docs, "BASE_DIR", base_dir):
            failures = check_agent_docs.check_doc_links()

        self.assertIn("docs/DOMAIN_MODEL.md does not link/reference AGENTS.md", failures)

    def test_missing_targeted_check_command_is_reported(self):
        base_dir = self.build_docs(
            overrides={
                "AGENTS.md": agents_doc().replace(
                    "python scripts/check_doc_drift.py",
                    "",
                )
            }
        )
        with patch.object(check_agent_docs, "BASE_DIR", base_dir):
            failures = check_agent_docs.check_agents_commands(
                expected_scripts=["scripts/check_doc_drift.py"]
            )

        self.assertIn(
            "AGENTS.md missing targeted check command: "
            "python scripts/check_doc_drift.py",
            failures,
        )

    def test_missing_drift_checklist_make_target_is_reported(self):
        base_dir = self.build_docs(
            overrides={
                "docs/DRIFT_CHECKLIST.md": drift_checklist_doc().replace(
                    "make doc-drift",
                    "",
                )
            }
        )
        with patch.object(check_agent_docs, "BASE_DIR", base_dir):
            failures = check_agent_docs.check_drift_checklist_targets(
                expected_targets=["doc-drift"]
            )

        self.assertIn(
            "docs/DRIFT_CHECKLIST.md missing focused check target: "
            "make doc-drift",
            failures,
        )

    def test_missing_contributing_make_target_is_reported(self):
        base_dir = self.build_docs(
            overrides={
                "CONTRIBUTING.md": contributing_doc().replace(
                    "make local-smoke",
                    "",
                )
            }
        )
        with patch.object(check_agent_docs, "BASE_DIR", base_dir):
            failures = check_agent_docs.check_contributing_targets(
                expected_targets=["local-smoke"]
            )

        self.assertIn(
            "CONTRIBUTING.md missing focused check target: make local-smoke",
            failures,
        )


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AgentDocsRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
