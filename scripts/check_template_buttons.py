#!/usr/bin/env python
"""Check template buttons declare an explicit type."""

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
BUTTON_RE = re.compile(r"<button\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(
    r"""(?P<name>[\w:-]+)(?:\s*=\s*(?P<quote>["'])(?P<quoted>.*?)(?P=quote)|\s*=\s*(?P<bare>[^\s>]+))?""",
    re.DOTALL,
)
VALID_BUTTON_TYPES = {"button", "submit", "reset"}

CHECK_DESCRIPTION = """\
Template button smoke check:
- scans local Django templates.
- requires every <button> tag to declare type.
- validates literal button types are button, submit, or reset.
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


def parse_attrs(attrs: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for match in ATTR_RE.finditer(attrs):
        name = match.group("name").lower()
        value = match.group("quoted") if match.group("quoted") is not None else match.group("bare")
        parsed[name] = value or ""
    return parsed


def template_button_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    relative_path = path.relative_to(BASE_DIR)

    for match in BUTTON_RE.finditer(text):
        attrs = parse_attrs(match.group("attrs"))
        button_type = attrs.get("type")
        line = line_number(text, match.start())
        if button_type is None:
            findings.append(
                Finding(
                    path=relative_path,
                    line_number=line,
                    message='button tag must include an explicit type attribute',
                )
            )
            continue
        if "{{" in button_type or "{%" in button_type:
            continue
        if button_type.strip().lower() not in VALID_BUTTON_TYPES:
            findings.append(
                Finding(
                    path=relative_path,
                    line_number=line,
                    message=f"button type must be one of: {', '.join(sorted(VALID_BUTTON_TYPES))}",
                )
            )

    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(template_button_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nTemplate button check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nTemplate button check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
