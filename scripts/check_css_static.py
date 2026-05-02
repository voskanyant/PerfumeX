#!/usr/bin/env python
"""Check lightweight CSS style-system rules for app static files."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from smoke_env import BASE_DIR


STATIC_DIRS = (
    BASE_DIR / "prices" / "static",
    BASE_DIR / "assistant_core" / "static",
    BASE_DIR / "assistant_linking" / "static",
    BASE_DIR / "catalog" / "static",
)
COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
MERGE_MARKER_RE = re.compile(r"^(<<<<<<<|=======|>>>>>>>)", re.MULTILINE)
NEGATIVE_LETTER_SPACING_RE = re.compile(
    r"letter-spacing\s*:\s*-[^;]+;",
    re.IGNORECASE,
)
VIEWPORT_FONT_SIZE_RE = re.compile(
    r"font-size\s*:\s*[^;]*(?:vw|vh|vmin|vmax)[^;]*;",
    re.IGNORECASE,
)

CHECK_DESCRIPTION = """\
CSS rule smoke check:
- discovers app static .css files.
- blocks unresolved merge markers.
- checks balanced braces after stripping comments.
- enforces stable typography rules from docs/UI_DESIGN_SYSTEM.md.
"""


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    message: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def css_files() -> list[Path]:
    files: list[Path] = []
    for static_dir in STATIC_DIRS:
        if not static_dir.exists():
            continue
        files.extend(path for path in static_dir.rglob("*.css") if path.is_file())
    return sorted(files)


def line_number(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def stripped_css(text: str) -> str:
    return COMMENT_RE.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def brace_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    stack: list[int] = []
    for index, char in enumerate(stripped_css(text)):
        if char == "{":
            stack.append(index)
        elif char == "}":
            if stack:
                stack.pop()
            else:
                findings.append(
                    Finding(
                        path=path.relative_to(BASE_DIR),
                        line_number=line_number(text, index),
                        message="unmatched closing brace",
                    )
                )
    for index in stack:
        findings.append(
            Finding(
                path=path.relative_to(BASE_DIR),
                line_number=line_number(text, index),
                message="unmatched opening brace",
            )
        )
    return findings


def css_rule_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    relative_path = path.relative_to(BASE_DIR)

    for match in MERGE_MARKER_RE.finditer(text):
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="unresolved merge marker",
            )
        )
    for match in NEGATIVE_LETTER_SPACING_RE.finditer(text):
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="negative letter-spacing is not allowed; use 0",
            )
        )
    for match in VIEWPORT_FONT_SIZE_RE.finditer(text):
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="viewport-scaled font-size is not allowed",
            )
        )

    findings.extend(brace_findings(path, text))
    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(css_rule_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = css_files()
    findings = all_findings(files)
    if findings:
        print("\nCSS rule check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nCSS rule check passed for {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
