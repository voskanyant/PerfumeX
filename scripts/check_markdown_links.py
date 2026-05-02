#!/usr/bin/env python
"""Check local Markdown links in repository documentation."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote


BASE_DIR = Path(__file__).resolve().parents[1]
DOC_GLOBS = [
    "*.md",
    "docs/*.md",
    "assistant_linking/docs/*.md",
]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
SKIP_SCHEMES = (
    "http://",
    "https://",
    "mailto:",
    "tel:",
    "app://",
    "plugin://",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def markdown_files() -> list[Path]:
    files: list[Path] = []
    for pattern in DOC_GLOBS:
        files.extend(path for path in BASE_DIR.glob(pattern) if path.is_file())
    return sorted(set(files))


def normalize_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        return unquote(target[1:-1].strip())
    if " " in target:
        target = target.split(" ", 1)[0]
    return unquote(target)


def is_external_or_anchor(target: str) -> bool:
    if not target or target.startswith("#"):
        return True
    return target.lower().startswith(SKIP_SCHEMES)


def strip_anchor(target: str) -> str:
    return target.split("#", 1)[0]


def check_file(path: Path) -> list[tuple[int, str]]:
    failures: list[tuple[int, str]] = []
    text = path.read_text(encoding="utf-8-sig")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in LINK_RE.finditer(line):
            target = normalize_target(match.group(1))
            if is_external_or_anchor(target):
                continue
            target_path_text = strip_anchor(target)
            if not target_path_text:
                continue
            target_path = (path.parent / target_path_text).resolve()
            try:
                target_path.relative_to(BASE_DIR)
            except ValueError:
                failures.append((line_number, target))
                continue
            if not target_path.exists():
                failures.append((line_number, target))
    return failures


def main(quiet: bool = False) -> int:
    files = markdown_files()
    failures: list[tuple[Path, int, str]] = []
    if not quiet:
        print("Markdown local-link check:", flush=True)
    for path in files:
        relative_path = path.relative_to(BASE_DIR)
        if not quiet:
            print(f"Checking {relative_path}", flush=True)
        for line_number, target in check_file(path):
            failures.append((relative_path, line_number, target))

    if failures:
        print("\nMarkdown local-link check failed:")
        for path, line_number, target in failures:
            print(f"- {path}:{line_number}: missing local target {target}")
        return 1

    print(f"\nMarkdown local-link check passed for {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
