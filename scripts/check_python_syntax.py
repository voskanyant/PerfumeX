#!/usr/bin/env python
"""Run dependency-light Python syntax checks for source and smoke scripts."""

from __future__ import annotations

import argparse
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIRS = [
    "perfumex",
    "prices",
    "catalog",
    "assistant_core",
    "assistant_linking",
    "scripts",
]
IGNORED_PATH_PREFIXES = (
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "logs/",
    "media/",
    "node_modules/",
    "staticfiles/",
    "tmp/",
    "tmp_",
)
IGNORED_PATH_PARTS = {".cache", "__pycache__"}

CHECK_DESCRIPTION = """\
Python syntax smoke check:
- discovers project/app/script .py files.
- compiles each file without writing bytecode.
- skips runtime, cache, dependency, media, and scratch paths.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def is_ignored_path(path: Path) -> bool:
    relative_path = path.relative_to(BASE_DIR).as_posix()
    parts = set(relative_path.split("/"))
    return relative_path.startswith(IGNORED_PATH_PREFIXES) or bool(
        parts & IGNORED_PATH_PARTS
    )


def python_files() -> list[Path]:
    files: list[Path] = []
    for source_dir_name in SOURCE_DIRS:
        source_dir = BASE_DIR / source_dir_name
        if not source_dir.exists():
            continue
        files.extend(
            path
            for path in source_dir.rglob("*.py")
            if path.is_file() and not is_ignored_path(path)
        )
    return sorted(files)


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    failures: list[tuple[Path, str]] = []
    files = python_files()
    for path in files:
        relative_path = path.relative_to(BASE_DIR)
        if not quiet:
            print(f"Checking {relative_path}", flush=True)
        try:
            source = path.read_text(encoding="utf-8-sig")
            compile(source, str(relative_path), "exec")
        except (OSError, SyntaxError, ValueError) as exc:
            failures.append((relative_path, str(exc)))

    if failures:
        print("\nPython syntax check failed:")
        for path, message in failures:
            print(f"- {path}: {message}")
        return 1

    print(f"\nPython syntax check passed for {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
