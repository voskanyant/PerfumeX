#!/usr/bin/env python
"""Check lightweight Django template accessibility conventions."""

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
ACTION_TAG_RE = re.compile(r"<(?P<tag>a|button)\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
IMG_TAG_RE = re.compile(r"<img\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
INPUT_TAG_RE = re.compile(r"<input\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
CHOICE_INPUT_RE = re.compile(
    r"<input\b(?P<attrs>[^>]*\btype\s*=\s*['\"](?:checkbox|radio)['\"][^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
ATTR_RE = re.compile(
    r"""(?P<name>[\w:-]+)(?:\s*=\s*(?P<quote>["'])(?P<quoted>.*?)(?P=quote)|\s*=\s*(?P<bare>[^\s>]+))?""",
    re.DOTALL,
)

CHECK_DESCRIPTION = """\
Template accessibility smoke check:
- scans local Django templates.
- requires icon-only button/link actions to have title and aria-label.
- requires image tags to have alt text, including alt="" for decorative images.
- requires checkbox/radio inputs to have aria-label, aria-labelledby, title, or an associated label.
- requires text/search inputs to have aria-label, aria-labelledby, title, or an associated label.
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


def class_tokens(attrs: dict[str, str]) -> set[str]:
    return set(attrs.get("class", "").lower().split())


def label_for_ids(text: str) -> set[str]:
    ids: set[str] = set()
    for match in re.finditer(r"<label\b(?P<attrs>[^>]*)>", text, re.IGNORECASE | re.DOTALL):
        attrs = parse_attrs(match.group("attrs"))
        label_for = attrs.get("for", "").strip()
        if label_for:
            ids.add(label_for)
    return ids


def is_wrapped_by_label(text: str, index: int) -> bool:
    before = text[:index].lower()
    last_label_open = before.rfind("<label")
    last_label_close = before.rfind("</label")
    if last_label_open == -1 or last_label_close > last_label_open:
        return False
    next_label_close = text[index:].lower().find("</label>")
    return next_label_close != -1


def input_has_accessible_label(*, attrs: dict[str, str], labelled_ids: set[str], text: str, index: int) -> bool:
    for attr_name in ("aria-label", "aria-labelledby", "title"):
        if attrs.get(attr_name, "").strip():
            return True

    input_id = attrs.get("id", "").strip()
    if input_id and input_id in labelled_ids:
        return True

    return is_wrapped_by_label(text, index)


def text_input_needs_label(attrs: dict[str, str]) -> bool:
    input_type = attrs.get("type", "text").strip().lower()
    return input_type in {"", "text", "search"}


def template_accessibility_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    relative_path = path.relative_to(BASE_DIR)
    labelled_ids = label_for_ids(text)

    for match in ACTION_TAG_RE.finditer(text):
        attrs = parse_attrs(match.group("attrs"))
        classes = class_tokens(attrs)
        if not {"button", "icon"}.issubset(classes):
            continue
        line = line_number(text, match.start())
        if not attrs.get("title", "").strip():
            findings.append(
                Finding(
                    path=relative_path,
                    line_number=line,
                    message='icon-only button/link must include a non-empty title attribute',
                )
            )
        if not attrs.get("aria-label", "").strip():
            findings.append(
                Finding(
                    path=relative_path,
                    line_number=line,
                    message='icon-only button/link must include a non-empty aria-label attribute',
                )
            )

    for match in IMG_TAG_RE.finditer(text):
        attrs = parse_attrs(match.group("attrs"))
        if "alt" not in attrs:
            findings.append(
                Finding(
                    path=relative_path,
                    line_number=line_number(text, match.start()),
                    message='image tag must include alt text, or alt="" when decorative',
                )
            )

    for match in CHOICE_INPUT_RE.finditer(text):
        attrs = parse_attrs(match.group("attrs"))
        if input_has_accessible_label(
            attrs=attrs,
            labelled_ids=labelled_ids,
            text=text,
            index=match.start(),
        ):
            continue
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="checkbox/radio input must have an accessible label",
            )
        )

    for match in INPUT_TAG_RE.finditer(text):
        attrs = parse_attrs(match.group("attrs"))
        if not text_input_needs_label(attrs):
            continue
        if input_has_accessible_label(
            attrs=attrs,
            labelled_ids=labelled_ids,
            text=text,
            index=match.start(),
        ):
            continue
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="text/search input must have an accessible label",
            )
        )

    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(template_accessibility_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nTemplate accessibility check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nTemplate accessibility check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
