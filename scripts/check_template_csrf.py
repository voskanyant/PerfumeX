#!/usr/bin/env python
"""Check template POST forms include CSRF tokens."""

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
FORM_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", re.IGNORECASE | re.DOTALL)
METHOD_POST_RE = re.compile(r"""method\s*=\s*["']post["']""", re.IGNORECASE)
CSRF_TOKEN_RE = re.compile(r"{%\s*csrf_token\s*%}")

CHECK_DESCRIPTION = """\
Template CSRF smoke check:
- scans local Django templates.
- checks literal method="post" forms.
- requires each POST form to include {% csrf_token %}.
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


def csrf_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for form_match in FORM_RE.finditer(text):
        if not METHOD_POST_RE.search(form_match.group("attrs")):
            continue
        if CSRF_TOKEN_RE.search(form_match.group("body")):
            continue
        line_number = text[: form_match.start()].count("\n") + 1
        findings.append(Finding(path=path.relative_to(BASE_DIR), line_number=line_number))
    return findings


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(csrf_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nPOST forms missing {% csrf_token %}:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}")
        return 1

    print(f"\nTemplate CSRF check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
