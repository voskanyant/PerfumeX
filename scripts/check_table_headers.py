#!/usr/bin/env python
"""Check Django template table headers for explicit column scope."""

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
TH_RE = re.compile(r"<th\b(?P<attrs>[^>]*)>(?P<body>.*?)</th>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
SCOPE_RE = re.compile(r"\bscope\s*=", re.IGNORECASE)
ACCESSIBLE_NAME_RE = re.compile(r"""\b(?:aria-label|title)\s*=\s*["'][^"']+["']""", re.IGNORECASE)

CHECK_DESCRIPTION = """\
Table header smoke check:
- scans local Django templates.
- checks table header cells.
- requires every <th> to declare scope, usually scope="col".
- requires visually empty header cells to have aria-label or title.
"""


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    message: str


def visible_header_text(body: str) -> str:
    text = TAG_RE.sub("", body)
    text = re.sub(r"{#[\s\S]*?#}", "", text)
    text = re.sub(r"{%[\s\S]*?%}", "", text)
    text = re.sub(r"{{[\s\S]*?}}", "", text)
    return " ".join(text.split())


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


def table_header_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in TH_RE.finditer(text):
        line_number = text[: match.start()].count("\n") + 1
        if SCOPE_RE.search(match.group("attrs")):
            if visible_header_text(match.group("body")) or ACCESSIBLE_NAME_RE.search(match.group("attrs")):
                continue
            findings.append(
                Finding(
                    path=path.relative_to(BASE_DIR),
                    line_number=line_number,
                    message="empty header missing aria-label or title",
                )
            )
            continue
        findings.append(
            Finding(
                path=path.relative_to(BASE_DIR),
                line_number=line_number,
                message="header missing scope",
            )
        )
    return findings


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(table_header_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nTable header issues:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number} - {finding.message}")
        return 1

    print(f"\nTable header check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
