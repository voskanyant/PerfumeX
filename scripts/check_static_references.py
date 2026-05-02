#!/usr/bin/env python
"""Verify literal Django static references in local templates."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from smoke_env import BASE_DIR, LOCAL_DJANGO_DEFAULTS, apply_defaults

TEMPLATE_DIRS = (
    BASE_DIR / "prices" / "templates",
    BASE_DIR / "assistant_core" / "templates",
    BASE_DIR / "assistant_linking" / "templates",
    BASE_DIR / "catalog" / "templates",
)
STATIC_TAG_RE = re.compile(r"""{%\s*static\s+["']([^"']+)["']\s*%}""")

CHECK_DESCRIPTION = """\
Static reference smoke check:
- scans local Django templates for literal {% static 'path' %} references.
- resolves each path through Django's staticfiles finder.
- catches stale CSS, JavaScript, and image references after UI changes.
"""


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


def static_references(path: Path) -> list[str]:
    return STATIC_TAG_RE.findall(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    args = parse_args()
    if not args.quiet:
        print(CHECK_DESCRIPTION, flush=True)
    sys.path.insert(0, str(BASE_DIR))
    apply_defaults(LOCAL_DJANGO_DEFAULTS)

    import django
    from django.contrib.staticfiles import finders

    django.setup()

    references: list[tuple[Path, str]] = []
    for template_file in template_files():
        for static_path in static_references(template_file):
            references.append((template_file, static_path))

    if not references:
        print("No literal static template references found.")
        return 0

    missing: list[tuple[Path, str]] = []
    for template_file, static_path in references:
        relative_template = template_file.relative_to(BASE_DIR)
        if not args.quiet:
            print(f"Checking {relative_template}: {static_path}", flush=True)
        if finders.find(static_path) is None:
            missing.append((relative_template, static_path))

    if missing:
        print("\nMissing static references:")
        for template_file, static_path in missing:
            print(f"- {template_file}: {static_path}")
        return 1

    print(f"\nStatic reference check passed for {len(references)} reference(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
