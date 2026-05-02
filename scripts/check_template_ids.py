#!/usr/bin/env python
"""Check templates do not repeat literal HTML ids."""

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
ID_RE = re.compile(
    r"""\bid\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)

CHECK_DESCRIPTION = """\
Template id smoke check:
- scans local Django templates.
- checks literal id="..." values.
- requires each literal id to be unique within its template.
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


def is_template_expression(value: str) -> bool:
    return "{{" in value or "{%" in value


def template_id_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[str, int] = {}
    relative_path = path.relative_to(BASE_DIR)

    for match in ID_RE.finditer(text):
        value = match.group("value")
        if is_template_expression(value):
            continue
        line = line_number(text, match.start())
        first_line = seen.get(value)
        if first_line is not None:
            findings.append(
                Finding(
                    path=relative_path,
                    line_number=line,
                    message=f'duplicate literal id "{value}" first appears on line {first_line}',
                )
            )
            continue
        seen[value] = line

    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(template_id_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nTemplate id check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nTemplate id check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
