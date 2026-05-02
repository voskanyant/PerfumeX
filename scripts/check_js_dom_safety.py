#!/usr/bin/env python
"""Check UI JavaScript surfaces for unsafe DOM HTML injection patterns."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIRS = [
    BASE_DIR / "prices" / "static",
    BASE_DIR / "assistant_core" / "static",
    BASE_DIR / "assistant_linking" / "static",
    BASE_DIR / "catalog" / "static",
]
TEMPLATE_DIRS = [
    BASE_DIR / "prices" / "templates",
    BASE_DIR / "assistant_core" / "templates",
    BASE_DIR / "assistant_linking" / "templates",
    BASE_DIR / "catalog" / "templates",
]
UNSAFE_PATTERNS = [
    re.compile(r"\.innerHTML\b"),
    re.compile(r"\.outerHTML\b"),
    re.compile(r"\binsertAdjacentHTML\s*\("),
    re.compile(r"\bdocument\.write\s*\("),
]
CHECK_DESCRIPTION = """\
JavaScript DOM safety check:
- discovers app static .js files and Django templates.
- rejects direct HTML injection APIs.
- prefer textContent, createTextNode, createElement, and appendChild for backend data.
"""


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
    for template_dir in TEMPLATE_DIRS:
        if not template_dir.exists():
            continue
        files.extend(path for path in template_dir.rglob("*.html") if path.is_file())
    return sorted(files)


def scan_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if any(pattern.search(line) for pattern in UNSAFE_PATTERNS):
            findings.append((line_number, line.strip()))
    return findings


def main() -> int:
    args = parse_args()
    if not args.quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = source_files()
    failures: list[tuple[Path, int, str]] = []
    for path in files:
        relative_path = path.relative_to(BASE_DIR)
        if not args.quiet:
            print(f"Checking {relative_path}", flush=True)
        for line_number, line in scan_file(path):
            failures.append((relative_path, line_number, line))

    if failures:
        print("\nJavaScript DOM safety check failed:")
        for path, line_number, line in failures:
            print(f"- {path}:{line_number}: {line}")
        print(
            "\nUse textContent/createTextNode/createElement for dynamic text. "
            "If trusted static HTML is truly required, document the exception before adding it.",
            file=sys.stderr,
        )
        return 1

    print(f"\nJavaScript DOM safety check passed for {len(files)} source file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
