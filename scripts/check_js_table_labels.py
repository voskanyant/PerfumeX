#!/usr/bin/env python
"""Check static JavaScript-generated table cells for mobile labels."""

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
CREATE_TD_RE = re.compile(
    r"""\b(?:var|let|const)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:document\.createElement\(\s*['"]td['"]\s*\)|el\(\s*['"]td['"])"""
)
DATA_LABEL_RE = r"""\b{name}\.(?:dataset\.label\s*=|setAttribute\(\s*['"]data-label['"])"""
COLSPAN_RE = r"""\b{name}\.(?:colSpan\s*=|setAttribute\(\s*['"]colspan['"])"""

CHECK_DESCRIPTION = """\
JavaScript table-label smoke check:
- scans app static .js files.
- finds table data cells created with document.createElement("td") or the local el("td") helper.
- requires generated cells to set data-label or colspan.
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


def source_files() -> list[Path]:
    files: list[Path] = []
    for static_dir in STATIC_DIRS:
        if not static_dir.exists():
            continue
        files.extend(path for path in static_dir.rglob("*.js") if path.is_file())
    return sorted(files)


def line_number(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def has_label_or_colspan(name: str, text: str, start_index: int) -> bool:
    window = text[start_index : start_index + 900]
    escaped_name = re.escape(name)
    return bool(
        re.search(DATA_LABEL_RE.format(name=escaped_name), window)
        or re.search(COLSPAN_RE.format(name=escaped_name), window)
    )


def js_table_label_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in CREATE_TD_RE.finditer(text):
        name = match.group("name")
        if has_label_or_colspan(name, text, match.end()):
            continue
        findings.append(
            Finding(
                path=path.relative_to(BASE_DIR),
                line_number=line_number(text, match.start()),
                message="generated table data cell needs data-label or colspan",
            )
        )
    return findings


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(js_table_label_findings(path, text))
    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = source_files()
    findings = all_findings(files)
    if findings:
        print("\nJavaScript table-label check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nJavaScript table-label check passed for {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
