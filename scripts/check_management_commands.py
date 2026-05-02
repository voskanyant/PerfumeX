#!/usr/bin/env python
"""Import all local Django management command modules."""

from __future__ import annotations

import argparse
import importlib
import sys

from smoke_env import BASE_DIR, LOCAL_APPS, LOCAL_DJANGO_DEFAULTS, apply_defaults

CHECK_DESCRIPTION = """\
Management command import smoke check:
- initializes Django with local-safe defaults.
- imports each local app management command module.
- catches broken imports after service extraction without running command logic.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def command_modules() -> list[str]:
    modules: list[str] = []
    for app_label in LOCAL_APPS:
        commands_dir = BASE_DIR / app_label / "management" / "commands"
        if not commands_dir.exists():
            continue
        for path in sorted(commands_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            modules.append(f"{app_label}.management.commands.{path.stem}")
    return modules


def main() -> int:
    args = parse_args()
    if not args.quiet:
        print(CHECK_DESCRIPTION, flush=True)
    sys.path.insert(0, str(BASE_DIR))
    apply_defaults(LOCAL_DJANGO_DEFAULTS)

    import django

    django.setup()

    modules = command_modules()
    if not modules:
        print("No local management commands found.")
        return 0

    failures: list[tuple[str, Exception]] = []
    for module_name in modules:
        if not args.quiet:
            print(f"Importing {module_name}", flush=True)
        try:
            importlib.import_module(module_name)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - smoke check reports any import failure.
            failures.append((module_name, exc))

    if failures:
        print("\nManagement command import check failed:")
        for module_name, exc in failures:
            print(f"- {module_name}: {exc.__class__.__name__}: {exc}")
        return 1

    print(f"\nManagement command import check passed for {len(modules)} module(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
