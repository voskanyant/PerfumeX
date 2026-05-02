#!/usr/bin/env python
"""Check that agent-facing repository memory docs keep their required shape."""

from __future__ import annotations

import argparse
from pathlib import Path

import check_make_targets


BASE_DIR = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = [
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
    "docs/REPO_MAP.md",
    "docs/DOMAIN_MODEL.md",
    "docs/WORKING_RULES.md",
    "docs/CODEX_TASKS.md",
    "docs/DECISIONS.md",
    "docs/UI_DESIGN_SYSTEM.md",
    "docs/DRIFT_CHECKLIST.md",
]
FOCUSED_DOCS = [
    "docs/REPO_MAP.md",
    "docs/DOMAIN_MODEL.md",
    "docs/WORKING_RULES.md",
    "docs/CODEX_TASKS.md",
    "docs/DECISIONS.md",
    "docs/UI_DESIGN_SYSTEM.md",
    "docs/DRIFT_CHECKLIST.md",
]
AGENTS_REQUIRED_TEXT = [
    "## Default task protocol",
    "Read this file first.",
    "docs/REPO_MAP.md",
    "docs/DOMAIN_MODEL.md",
    "docs/CODEX_TASKS.md",
    "docs/UI_DESIGN_SYSTEM.md",
    "docs/DECISIONS.md",
    "Code changed",
    "Docs changed",
    "Tests/checks run",
    "Follow-up notes",
    "The repo memory lives in files, not in chat.",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def read_text(relative_path: str) -> str:
    return (BASE_DIR / relative_path).read_text(encoding="utf-8-sig")


def check_required_files() -> list[str]:
    failures: list[str] = []
    for relative_path in REQUIRED_DOCS:
        path = BASE_DIR / relative_path
        if not path.is_file():
            failures.append(f"missing required doc: {relative_path}")
    return failures


def check_purpose_sections() -> list[str]:
    failures: list[str] = []
    for relative_path in ["AGENTS.md", *FOCUSED_DOCS]:
        text = read_text(relative_path)
        if "## Purpose of this document" not in text:
            failures.append(f"missing purpose section: {relative_path}")
    return failures


def check_agents_protocol() -> list[str]:
    failures: list[str] = []
    text = read_text("AGENTS.md")
    for required in AGENTS_REQUIRED_TEXT:
        if required not in text:
            failures.append(f"AGENTS.md missing required protocol text: {required}")
    return failures


def check_doc_links() -> list[str]:
    failures: list[str] = []
    agents = read_text("AGENTS.md")
    for relative_path in FOCUSED_DOCS:
        if relative_path not in agents:
            failures.append(f"AGENTS.md does not link/reference: {relative_path}")
    for relative_path in FOCUSED_DOCS:
        text = read_text(relative_path)
        if "AGENTS.md" not in text:
            failures.append(f"{relative_path} does not link/reference AGENTS.md")
    return failures


def expected_agent_command_scripts() -> list[str]:
    return sorted(set(check_make_targets.EXPECTED_TARGETS.values()))


def check_agents_commands(
    expected_scripts: list[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    text = read_text("AGENTS.md")

    for script_path in expected_scripts or expected_agent_command_scripts():
        command = f"python {script_path}"
        if command not in text:
            failures.append(f"AGENTS.md missing targeted check command: {command}")
    return failures


def expected_make_targets() -> list[str]:
    return sorted(check_make_targets.EXPECTED_TARGETS)


def check_drift_checklist_targets(
    expected_targets: list[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    text = read_text("docs/DRIFT_CHECKLIST.md")

    for target in expected_targets or expected_make_targets():
        command = f"make {target}"
        if command not in text:
            failures.append(
                "docs/DRIFT_CHECKLIST.md missing focused check target: "
                f"{command}"
            )
    return failures


def check_contributing_targets(
    expected_targets: list[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    text = read_text("CONTRIBUTING.md")

    for target in expected_targets or expected_make_targets():
        command = f"make {target}"
        if command not in text:
            failures.append(
                f"CONTRIBUTING.md missing focused check target: {command}"
            )
    return failures


def main(quiet: bool = False) -> int:
    failures = []
    failures.extend(check_required_files())
    if not failures:
        failures.extend(check_purpose_sections())
        failures.extend(check_agents_protocol())
        failures.extend(check_doc_links())
        failures.extend(check_agents_commands())
        failures.extend(check_drift_checklist_targets())
        failures.extend(check_contributing_targets())

    if failures:
        print("\nAgent documentation check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    if not quiet:
        print("Agent documentation check:")
        print("- required docs exist")
        print("- focused docs have purpose sections")
        print("- AGENTS.md keeps the default protocol and summary fields")
        print("- focused docs link back to AGENTS.md")
        print("- AGENTS.md lists focused repository check commands")
        print("- docs/DRIFT_CHECKLIST.md lists focused Makefile check targets")
        print("- CONTRIBUTING.md lists focused Makefile check targets")
    print(f"\nAgent documentation check passed for {len(REQUIRED_DOCS)} doc(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
