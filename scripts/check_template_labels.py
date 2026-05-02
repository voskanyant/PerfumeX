#!/usr/bin/env python
"""Check template label targets point at real controls."""

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
LABEL_RE = re.compile(r"<label\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
ID_RE = re.compile(
    r"""\bid\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)
ATTR_RE = re.compile(
    r"""(?P<name>[\w:-]+)(?:\s*=\s*(?P<quote>["'])(?P<quoted>.*?)(?P=quote)|\s*=\s*(?P<bare>[^\s>]+))?""",
    re.DOTALL,
)

CHECK_DESCRIPTION = """\
Template label smoke check:
- scans local Django templates.
- checks literal label for="..." targets.
- allows Django-rendered form fields that provide their ids at render time.
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


def literal_ids(text: str) -> set[str]:
    ids: set[str] = set()
    for match in ID_RE.finditer(text):
        value = match.group("value")
        if "{{" in value or "{%" in value:
            continue
        ids.add(value)
    return ids


def has_django_rendered_field(text: str, target: str) -> bool:
    if not target.startswith("id_"):
        return False
    field_name = target.removeprefix("id_")
    pattern = re.compile(
        r"{{\s*[\w.]+\." + re.escape(field_name) + r"(?:\s*[|}]|\.)",
        re.DOTALL,
    )
    return bool(pattern.search(text))


def is_template_expression(value: str) -> bool:
    return "{{" in value or "{%" in value


def template_label_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    ids = literal_ids(text)
    relative_path = path.relative_to(BASE_DIR)

    for match in LABEL_RE.finditer(text):
        attrs = parse_attrs(match.group("attrs"))
        target = attrs.get("for")
        if not target or is_template_expression(target):
            continue
        if target in ids or has_django_rendered_field(text, target):
            continue
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message=f'label target "{target}" has no matching literal id or rendered form field',
            )
        )

    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(template_label_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nTemplate label check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nTemplate label check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
