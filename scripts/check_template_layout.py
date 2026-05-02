#!/usr/bin/env python
"""Check that full-page templates use the shared base layout and header pattern."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from smoke_env import BASE_DIR


TEMPLATE_DIRS = (
    BASE_DIR / "prices" / "templates",
    BASE_DIR / "assistant_core" / "templates",
    BASE_DIR / "assistant_linking" / "templates",
    BASE_DIR / "catalog" / "templates",
)
EXTENDS_BASE_RE = re.compile(r"""{%\s*extends\s+["']prices/base\.html["']\s*%}""")
PAGE_HEADER_RE = re.compile(
    r"""includes/page_header\.html|prices/_breadcrumbs\.html|class\s*=\s*["'][^"']*(?:page-header|page-head|products-page-header|supplier-import-hero|login-shell)""",
    re.IGNORECASE,
)

CHECK_DESCRIPTION = """\
Template layout smoke check:
- scans local Django templates.
- requires full-page templates to extend prices/base.html.
- requires full-page templates to use the shared page header/breadcrumb pattern or a documented exception.
- allows base.html, includes, components, and underscore-prefixed partials.
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


def is_partial_template(relative_path: Path) -> bool:
    parts = set(relative_path.parts)
    return (
        relative_path.name == "base.html"
        or relative_path.name.startswith("_")
        or "includes" in parts
        or "components" in parts
    )


def extends_shared_base(path: Path) -> bool:
    return bool(EXTENDS_BASE_RE.search(path.read_text(encoding="utf-8-sig")))


def has_page_header_pattern(path: Path) -> bool:
    return bool(PAGE_HEADER_RE.search(path.read_text(encoding="utf-8-sig")))


def layout_failures(paths: list[Path]) -> list[Path]:
    failures: list[Path] = []
    for path in paths:
        relative_path = path.relative_to(BASE_DIR)
        if is_partial_template(relative_path):
            continue
        if not extends_shared_base(path) or not has_page_header_pattern(path):
            failures.append(relative_path)
    return failures


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    files = template_files()
    failures = layout_failures(files)
    if failures:
        print("\nFull-page templates missing shared base layout or page header pattern:")
        for path in failures:
            print(f"- {path}")
        return 1

    print(f"\nTemplate layout check passed for {len(files)} template(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
