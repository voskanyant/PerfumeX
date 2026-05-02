#!/usr/bin/env python
"""Check static JavaScript for accessible generated choice controls."""

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
CREATE_INPUT_RE = re.compile(
    r"\b(?:var|let|const)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*document\.createElement\(\s*['\"]input['\"]\s*\)"
)
CREATE_LABEL_RE = re.compile(
    r"\b(?:var|let|const)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*document\.createElement\(\s*['\"]label['\"]\s*\)"
)
CHOICE_TYPE_RE = re.compile(
    r"\b(?P<name>[A-Za-z_$][\w$]*)\.(?:type\s*=\s*['\"](?:checkbox|radio)['\"]|setAttribute\(\s*['\"]type['\"]\s*,\s*['\"](?:checkbox|radio)['\"]\s*\))"
)
ACCESSIBLE_LABEL_RE = r"""\b{name}\.(?:setAttribute\(\s*['"](?:aria-label|aria-labelledby|title)['"]|title\s*=)"""
WRAPPED_BY_LABEL_RE = r"""\b{label_name}\.appendChild\(\s*{input_name}\s*\)"""

CHECK_DESCRIPTION = """\
JavaScript accessibility smoke check:
- scans app static .js files.
- finds inputs created with document.createElement("input").
- requires generated checkbox/radio inputs to receive an accessible name or be appended to a label.
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


def has_accessible_name_or_wrapper(name: str, label_names: set[str], text: str, start_index: int) -> bool:
    window = text[start_index : start_index + 700]
    escaped_name = re.escape(name)
    label_re = re.compile(ACCESSIBLE_LABEL_RE.format(name=escaped_name))
    if label_re.search(window):
        return True
    for label_name in label_names:
        wrapper_re = re.compile(
            WRAPPED_BY_LABEL_RE.format(
                label_name=re.escape(label_name),
                input_name=escaped_name,
            )
        )
        if wrapper_re.search(window):
            return True
    return False


def js_accessibility_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    input_creations = {
        match.group("name"): match.start()
        for match in CREATE_INPUT_RE.finditer(text)
    }
    label_names = {
        match.group("name")
        for match in CREATE_LABEL_RE.finditer(text)
    }
    for match in CHOICE_TYPE_RE.finditer(text):
        name = match.group("name")
        create_index = input_creations.get(name)
        if create_index is None:
            continue
        if has_accessible_name_or_wrapper(name, label_names, text, match.end()):
            continue
        findings.append(
            Finding(
                path=path.relative_to(BASE_DIR),
                line_number=line_number(text, create_index),
                message="generated checkbox/radio input needs aria-label, aria-labelledby, title, or label wrapper",
            )
        )
    return findings


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(js_accessibility_findings(path, text))
    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = source_files()
    findings = all_findings(files)
    if findings:
        print("\nJavaScript accessibility check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nJavaScript accessibility check passed for {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
