#!/usr/bin/env python
"""Verify literal Django template URL names exist."""

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
URL_TAG_RE = re.compile(r"""{%\s*url\s+["']([^"']+)["']""")

CHECK_DESCRIPTION = """\
Template URL smoke check:
- scans local Django templates for literal {% url 'name' %} references.
- verifies each named route exists, including namespaced routes.
- does not reverse URLs, so it does not need placeholder arguments.
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


def url_references(path: Path) -> list[str]:
    return URL_TAG_RE.findall(path.read_text(encoding="utf-8-sig"))


def named_url_exists(resolver, route_name: str) -> bool:
    parts = route_name.split(":")
    if len(parts) == 1:
        return route_name in resolver.reverse_dict

    current_resolver = resolver
    for namespace in parts[:-1]:
        namespace_entry = current_resolver.namespace_dict.get(namespace)
        if namespace_entry is None:
            return False
        current_resolver = namespace_entry[1]
    return parts[-1] in current_resolver.reverse_dict


def main() -> int:
    args = parse_args()
    if not args.quiet:
        print(CHECK_DESCRIPTION, flush=True)
    sys.path.insert(0, str(BASE_DIR))
    apply_defaults(LOCAL_DJANGO_DEFAULTS)

    import django
    from django.urls import get_resolver

    django.setup()
    resolver = get_resolver()

    references: list[tuple[Path, str]] = []
    for template_file in template_files():
        for route_name in url_references(template_file):
            references.append((template_file, route_name))

    if not references:
        print("No literal template URL references found.")
        return 0

    missing: list[tuple[Path, str]] = []
    for template_file, route_name in references:
        relative_template = template_file.relative_to(BASE_DIR)
        if not args.quiet:
            print(f"Checking {relative_template}: {route_name}", flush=True)
        if not named_url_exists(resolver, route_name):
            missing.append((relative_template, route_name))

    if missing:
        print("\nMissing template URL names:")
        for template_file, route_name in missing:
            print(f"- {template_file}: {route_name}")
        return 1

    print(f"\nTemplate URL check passed for {len(references)} reference(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
