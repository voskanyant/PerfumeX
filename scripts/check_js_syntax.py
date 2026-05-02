#!/usr/bin/env python
"""Run dependency-light JavaScript syntax checks for app static files."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIRS = [
    BASE_DIR / "prices" / "static",
    BASE_DIR / "assistant_core" / "static",
    BASE_DIR / "assistant_linking" / "static",
    BASE_DIR / "catalog" / "static",
]

CHECK_DESCRIPTION = """\
JavaScript syntax smoke check:
- discovers app static .js files.
- runs node --check on each file.
- provides a lightweight fallback when npm run lint:js is unavailable locally.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def js_files() -> list[Path]:
    files: list[Path] = []
    for static_dir in STATIC_DIRS:
        if not static_dir.exists():
            continue
        files.extend(path for path in static_dir.rglob("*.js") if path.is_file())
    return sorted(files)


def main() -> int:
    args = parse_args()
    if not args.quiet:
        print(CHECK_DESCRIPTION, flush=True)
    node = shutil.which("node")
    if not node:
        print(
            "Node.js is required for this check but was not found on PATH.",
            file=sys.stderr,
        )
        return 1

    files = js_files()
    if not files:
        print("No static JavaScript files found.")
        return 0

    failures: list[tuple[Path, int]] = []
    for path in files:
        relative_path = path.relative_to(BASE_DIR)
        if not args.quiet:
            print(f"Checking {relative_path}", flush=True)
        completed = subprocess.run(
            [node, "--check", str(path)], cwd=BASE_DIR, check=False
        )
        if completed.returncode:
            failures.append((relative_path, completed.returncode))

    if failures:
        print("\nJavaScript syntax check failed:")
        for path, returncode in failures:
            print(f"- {path}: exit {returncode}")
        return 1

    print(f"\nJavaScript syntax check passed for {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
