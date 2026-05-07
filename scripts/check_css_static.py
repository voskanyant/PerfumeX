#!/usr/bin/env python
"""Check lightweight CSS style-system rules for app static files."""

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
COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
MERGE_MARKER_RE = re.compile(r"^(<<<<<<<|=======|>>>>>>>)", re.MULTILINE)
NEGATIVE_LETTER_SPACING_RE = re.compile(
    r"letter-spacing\s*:\s*-[^;]+;",
    re.IGNORECASE,
)
VIEWPORT_FONT_SIZE_RE = re.compile(
    r"font-size\s*:\s*[^;]*(?:vw|vh|vmin|vmax)[^;]*;",
    re.IGNORECASE,
)
MEDIA_RE = re.compile(r"@media\s*\((?P<condition>[^)]*)\)\s*{", re.IGNORECASE)
MAX_WIDTH_RE = re.compile(r"max-width\s*:\s*(?P<width>\d+(?:\.\d+)?)px", re.IGNORECASE)
RULE_RE = re.compile(r"(?P<selectors>[^{}@]+){(?P<declarations>[^{}]+)}", re.DOTALL)
TOUCH_TARGET_DECL_RE = re.compile(
    r"(?:^|;)\s*(?P<property>width|height|min-width|min-height)\s*:\s*(?P<value>\d+(?:\.\d+)?)px\b",
    re.IGNORECASE,
)
MOBILE_TOUCH_TARGET_SELECTORS = (
    ".button.icon",
    ".btn-icon",
    ".drawer-close",
    ".flash-close",
    ".search-clear-text",
    ".catalogue-linking-option",
    ".fragrantica-row-link",
    ".our-products-edit-button",
)

CHECK_DESCRIPTION = """\
CSS rule smoke check:
- discovers app static .css files.
- blocks unresolved merge markers.
- checks balanced braces after stripping comments.
- enforces stable typography rules from docs/UI_DESIGN_SYSTEM.md.
- enforces 42px mobile touch targets for shared icon/action controls.
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


def css_files() -> list[Path]:
    files: list[Path] = []
    for static_dir in STATIC_DIRS:
        if not static_dir.exists():
            continue
        files.extend(path for path in static_dir.rglob("*.css") if path.is_file())
    return sorted(files)


def line_number(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def stripped_css(text: str) -> str:
    return COMMENT_RE.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def brace_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    stack: list[int] = []
    for index, char in enumerate(stripped_css(text)):
        if char == "{":
            stack.append(index)
        elif char == "}":
            if stack:
                stack.pop()
            else:
                findings.append(
                    Finding(
                        path=path.relative_to(BASE_DIR),
                        line_number=line_number(text, index),
                        message="unmatched closing brace",
                    )
                )
    for index in stack:
        findings.append(
            Finding(
                path=path.relative_to(BASE_DIR),
                line_number=line_number(text, index),
                message="unmatched opening brace",
            )
        )
    return findings


def media_block_end(text: str, open_brace_index: int) -> int | None:
    depth = 0
    for index in range(open_brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def mobile_media_blocks(text: str) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    for match in MEDIA_RE.finditer(text):
        width_match = MAX_WIDTH_RE.search(match.group("condition"))
        if not width_match:
            continue
        if float(width_match.group("width")) > 767.98:
            continue
        block_start = match.end()
        block_end = media_block_end(text, block_start - 1)
        if block_end is None:
            continue
        blocks.append((block_start, text[block_start:block_end]))
    return blocks


def mobile_touch_target_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    relative_path = path.relative_to(BASE_DIR)
    for block_start, block in mobile_media_blocks(stripped_css(text)):
        for rule_match in RULE_RE.finditer(block):
            selectors = rule_match.group("selectors")
            if not any(
                selector in selectors for selector in MOBILE_TOUCH_TARGET_SELECTORS
            ):
                continue
            declarations = rule_match.group("declarations")
            for declaration_match in TOUCH_TARGET_DECL_RE.finditer(declarations):
                value = float(declaration_match.group("value"))
                if value >= 42:
                    continue
                findings.append(
                    Finding(
                        path=relative_path,
                        line_number=line_number(
                            text,
                            block_start
                            + rule_match.start()
                            + declaration_match.start(),
                        ),
                        message=(
                            f"mobile {declaration_match.group('property')} for shared action controls "
                            "must be at least 42px"
                        ),
                    )
                )
    return findings


def css_rule_findings(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    relative_path = path.relative_to(BASE_DIR)

    for match in MERGE_MARKER_RE.finditer(text):
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="unresolved merge marker",
            )
        )
    for match in NEGATIVE_LETTER_SPACING_RE.finditer(text):
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="negative letter-spacing is not allowed; use 0",
            )
        )
    for match in VIEWPORT_FONT_SIZE_RE.finditer(text):
        findings.append(
            Finding(
                path=relative_path,
                line_number=line_number(text, match.start()),
                message="viewport-scaled font-size is not allowed",
            )
        )

    findings.extend(mobile_touch_target_findings(path, text))
    findings.extend(brace_findings(path, text))
    return sorted(
        findings,
        key=lambda finding: (str(finding.path), finding.line_number, finding.message),
    )


def all_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        findings.extend(css_rule_findings(path, text))
    return findings


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = css_files()
    findings = all_findings(files)
    if findings:
        print("\nCSS rule check failed:")
        for finding in findings:
            print(f"- {finding.path}:{finding.line_number}: {finding.message}")
        return 1

    print(f"\nCSS rule check passed for {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
