#!/usr/bin/env python
"""Check Django templates do not add inline styles."""

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
INLINE_STYLE_RE = re.compile(r"\bstyle\s*=", re.IGNORECASE)
STYLE_TAG_RE = re.compile(r"</?\s*style\b", re.IGNORECASE)

CHECK_DESCRIPTION = """\
Template inline-style smoke check:
- scans local Django templates.
- blocks style attributes and <style> blocks.
- keeps reusable visual rules in static CSS files.
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


def template_files() -> list[Path]:
    files: list[Path] = []
    for template_dir in TEMPLATE_DIRS:
        if not template_dir.exists():
            continue
        files.extend(path for path in template_dir.rglob("*.html") if path.is_file())
    return sorted(files)


def line_number(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def inline_style_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    relative_path = path.relative_to(BASE_DIR)

    for match in INLINE_STYLE_RE.finditer(text):
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="inline style attribute; move reusable styling to static CSS",
            )
        )

    for match in STYLE_TAG_RE.finditer(text):
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="template <style> block; move reusable styling to static CSS",
            )
        )

    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(inline_style_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nTemplate inline-style check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nTemplate inline-style check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
