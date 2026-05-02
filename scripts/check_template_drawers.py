#!/usr/bin/env python
"""Check drawer and dialog accessibility conventions in Django templates."""

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
TAG_RE = re.compile(
    r"<(?P<tag>[a-z][\w:-]*)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
OPEN_TAG_RE = re.compile(r"<(?P<tag>[a-z][\w:-]*)\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(
    r"""(?P<name>[\w:-]+)(?:\s*=\s*(?P<quote>["'])(?P<quoted>.*?)(?P=quote)|\s*=\s*(?P<bare>[^\s>]+))?""",
    re.DOTALL,
)

CHECK_DESCRIPTION = """\
Template drawer/dialog smoke check:
- scans local Django templates.
- checks drawer toggles declare aria-controls and aria-expanded.
- checks drawer panels use app-drawer, aria-hidden, and an accessible label.
- checks drawer panels include an accessible close control.
- checks native dialog elements have an accessible label.
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


def id_values(text: str) -> set[str]:
    ids: set[str] = set()
    for match in OPEN_TAG_RE.finditer(text):
        element_id = parse_attrs(match.group("attrs")).get("id", "").strip()
        if element_id:
            ids.add(element_id)
    return ids


def has_template_value(value: str) -> bool:
    return "{{" in value or "{%" in value


def labelledby_target_exists(labelledby: str, ids: set[str]) -> bool:
    if not labelledby or has_template_value(labelledby):
        return True
    return all(label_id in ids for label_id in labelledby.split())


def tag_text(body: str) -> str:
    return re.sub(r"<[^>]+>", "", body).strip()


def element_body(text: str, match: re.Match[str]) -> str:
    close = re.search(rf"</{re.escape(match.group('tag'))}\s*>", text[match.end() :], re.IGNORECASE)
    if not close:
        return ""
    return text[match.end() : match.end() + close.start()]


def has_accessible_close_control(body: str) -> bool:
    for match in OPEN_TAG_RE.finditer(body):
        attrs = parse_attrs(match.group("attrs"))
        if "data-drawer-close" not in attrs:
            continue
        if attrs.get("aria-label", "").strip() or attrs.get("title", "").strip():
            return True
        if tag_text(element_body(body, match)):
            return True
    return False


def template_drawer_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    relative_path = path.relative_to(BASE_DIR)
    ids = id_values(text)
    drawer_names: set[str] = set()
    drawer_ids: set[str] = set()

    for match in OPEN_TAG_RE.finditer(text):
        attrs = parse_attrs(match.group("attrs"))
        drawer_name = attrs.get("data-drawer")
        if drawer_name is None:
            continue
        drawer_names.add(drawer_name)
        drawer_id = attrs.get("id", "").strip()
        if drawer_id:
            drawer_ids.add(drawer_id)
        line = line_number(text, match.start())
        classes = class_tokens(attrs)
        if "app-drawer" not in classes:
            findings.append(Finding(relative_path, line, "drawer panel must include class app-drawer"))
        if attrs.get("aria-hidden") != "true":
            findings.append(Finding(relative_path, line, 'drawer panel must start with aria-hidden="true"'))
        if not attrs.get("aria-label", "").strip() and not attrs.get("aria-labelledby", "").strip():
            findings.append(Finding(relative_path, line, "drawer panel must have aria-label or aria-labelledby"))
        elif not labelledby_target_exists(attrs.get("aria-labelledby", ""), ids):
            findings.append(Finding(relative_path, line, "drawer aria-labelledby must reference an existing id"))
        if not has_accessible_close_control(element_body(text, match)):
            findings.append(Finding(relative_path, line, "drawer panel must include an accessible data-drawer-close control"))

    for match in OPEN_TAG_RE.finditer(text):
        attrs = parse_attrs(match.group("attrs"))
        toggle_name = attrs.get("data-drawer-toggle")
        if toggle_name is None:
            continue
        line = line_number(text, match.start())
        controls = attrs.get("aria-controls", "").strip()
        if not controls:
            findings.append(Finding(relative_path, line, "drawer toggle must include aria-controls"))
        elif not has_template_value(controls) and controls not in drawer_ids:
            findings.append(Finding(relative_path, line, "drawer toggle aria-controls must reference an existing drawer id"))
        if attrs.get("aria-expanded") != "false":
            findings.append(Finding(relative_path, line, 'drawer toggle must start with aria-expanded="false"'))
        if toggle_name not in drawer_names:
            findings.append(Finding(relative_path, line, "drawer toggle value must match a data-drawer panel"))

    for match in OPEN_TAG_RE.finditer(text):
        if match.group("tag").lower() != "dialog":
            continue
        attrs = parse_attrs(match.group("attrs"))
        line = line_number(text, match.start())
        if not attrs.get("aria-label", "").strip() and not attrs.get("aria-labelledby", "").strip():
            findings.append(Finding(relative_path, line, "dialog must have aria-label or aria-labelledby"))
        elif not labelledby_target_exists(attrs.get("aria-labelledby", ""), ids):
            findings.append(Finding(relative_path, line, "dialog aria-labelledby must reference an existing id"))

    return sorted(findings, key=lambda finding: (str(finding.path), finding.line_number, finding.message))


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(template_drawer_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    findings = all_findings(files)
    if findings:
        print("\nTemplate drawer/dialog check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nTemplate drawer/dialog check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
