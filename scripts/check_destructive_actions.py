#!/usr/bin/env python
"""Check destructive POST controls have an explicit confirmation prompt."""

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
ANCHOR_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
SUBMIT_CONTROL_RE = re.compile(
    r"<(?P<tag>button|input)\b(?P<attrs>[^>]*)>"
    r"(?P<body>.*?</button>)?",
    re.IGNORECASE | re.DOTALL,
)
ATTR_RE = re.compile(
    r"""(?P<name>[\w:-]+)(?:\s*=\s*(?P<quote>["'])(?P<quoted>.*?)(?P=quote)|\s*=\s*(?P<bare>[^\s>]+))?""",
    re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
DESTRUCTIVE_TEXT_RE = re.compile(
    r"\b(delete|clear|remove|cancel|exclude)\b",
    re.IGNORECASE,
)

CHECK_DESCRIPTION = """\
Destructive action smoke check:
- scans local Django templates.
- checks POST submit buttons/inputs that look destructive.
- requires a data-confirm attribute on the submit control or its form.
- checks delete confirmation links use danger button styling.
"""


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    label: str
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


def parse_attrs(attrs: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for match in ATTR_RE.finditer(attrs):
        name = match.group("name").lower()
        value = match.group("quoted") if match.group("quoted") is not None else match.group("bare")
        parsed[name] = value or ""
    return parsed


def normalize_label(text: str) -> str:
    text = TAG_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def control_label(tag: str, attrs: dict[str, str], body: str) -> str:
    if tag.lower() == "input":
        return attrs.get("value", "")
    if body.lower().endswith("</button>"):
        body = body[: -len("</button>")]
    return normalize_label(body)


def is_submit_control(tag: str, attrs: dict[str, str]) -> bool:
    control_type = attrs.get("type", "submit").lower()
    if tag.lower() == "button":
        return control_type in {"", "submit"}
    return control_type == "submit"


def is_destructive_control(attrs: dict[str, str], label: str) -> bool:
    classes = f" {attrs.get('class', '').lower()} "
    return " danger " in classes or DESTRUCTIVE_TEXT_RE.search(label) is not None


def is_delete_link(attrs: dict[str, str], label: str) -> bool:
    href = attrs.get("href", "").lower()
    return bool(re.search(r"\bdelete\b", label, re.IGNORECASE) or "_delete" in href or "/delete" in href)


def destructive_action_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for form_match in FORM_RE.finditer(text):
        form_attrs = parse_attrs(form_match.group("attrs"))
        if form_attrs.get("method", "").lower() != "post":
            continue
        if "data-confirm" in form_attrs:
            continue

        body = form_match.group("body")
        for control_match in SUBMIT_CONTROL_RE.finditer(body):
            attrs = parse_attrs(control_match.group("attrs"))
            tag = control_match.group("tag")
            if not is_submit_control(tag, attrs):
                continue
            label = control_label(tag, attrs, control_match.group("body") or "")
            if not is_destructive_control(attrs, label):
                continue
            if "data-confirm" in attrs:
                continue
            line_number = text[: form_match.start("body") + control_match.start()].count("\n") + 1
            findings.append(
                Finding(
                    path=path.relative_to(BASE_DIR),
                    line_number=line_number,
                    label=label or attrs.get("class", tag),
                    message="destructive POST control missing data-confirm",
                )
            )
    for anchor_match in ANCHOR_RE.finditer(text):
        attrs = parse_attrs(anchor_match.group("attrs"))
        label = normalize_label(anchor_match.group("body"))
        if not is_delete_link(attrs, label):
            continue
        classes = f" {attrs.get('class', '').lower()} "
        if " button " not in classes:
            continue
        if " danger " in classes:
            continue
        findings.append(
            Finding(
                path=path.relative_to(BASE_DIR),
                line_number=text[: anchor_match.start()].count("\n") + 1,
                label=label or attrs.get("href", "delete link"),
                message="delete confirmation link must use danger button styling",
            )
        )
    return findings


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(destructive_action_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nDestructive action check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message} ({finding.label})")
        return 1

    print(f"\nDestructive action check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
