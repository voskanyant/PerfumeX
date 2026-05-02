#!/usr/bin/env python
"""Check responsive table-mobile markup for mobile labels."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from smoke_env import BASE_DIR


TEMPLATE_DIRS = (
    BASE_DIR / "prices" / "templates",
    BASE_DIR / "assistant_core" / "templates",
    BASE_DIR / "assistant_linking" / "templates",
    BASE_DIR / "catalog" / "templates",
)
TABLE_RE = re.compile(r"<table\b(?P<attrs>[^>]*)>(?P<body>.*?)</table>", re.IGNORECASE | re.DOTALL)
TD_RE = re.compile(r"<td\b(?P<attrs>[^>]*)>", re.IGNORECASE)
CLASS_RE = re.compile(r"""class\s*=\s*["'](?P<classes>[^"']*)["']""", re.IGNORECASE)

CHECK_DESCRIPTION = """\
Mobile table smoke check:
- scans local Django templates.
- checks tables using table-mobile.
- requires each data cell to have data-label or colspan.
"""


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def template_files() -> list[Path]:
    files: list[Path] = []
    for template_dir in TEMPLATE_DIRS:
        if not template_dir.exists():
            continue
        files.extend(path for path in template_dir.rglob("*.html") if path.is_file())
    return sorted(files)


def class_names(attrs: str) -> set[str]:
    match = CLASS_RE.search(attrs)
    if not match:
        return set()
    return set(match.group("classes").split())


def table_mobile_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for table_match in TABLE_RE.finditer(text):
        if "table-mobile" not in class_names(table_match.group("attrs")):
            continue

        body_start = table_match.start("body")
        for td_match in TD_RE.finditer(table_match.group("body")):
            attrs = td_match.group("attrs")
            if "data-label" in attrs or "colspan" in attrs:
                continue
            line_number = text[: body_start + td_match.start()].count("\n") + 1
            findings.append(Finding(path=path.relative_to(BASE_DIR), line_number=line_number))
    return findings


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(table_mobile_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nTable-mobile cells missing data-label or colspan:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}")
        return 1

    print(f"\nMobile table check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
