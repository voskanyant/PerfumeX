from __future__ import annotations

import argparse
import sys

from django.core.management import execute_from_command_line

from smoke_env import BASE_DIR, LOCAL_DJANGO_DEFAULTS, apply_defaults

UI_PARTIAL_TESTS = [
    "prices.test_ui_partials",
]
CHECK_DESCRIPTION = [
    "UI partial boundary check:",
    "- reusable UI should use shared includes under prices/templates/includes/.",
    "- page headers, tabs, pagination, table-empty rows, and empty states should not be hand-rolled unless explicitly allowlisted.",
    "- product-list and supplier-import headers are specialized exceptions documented in docs/UI_DESIGN_SYSTEM.md.",
    "- JavaScript-updated pagination must keep the shared pagination classes and stable container hooks.",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run UI partial boundary checks.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print Django test failures.",
    )
    return parser.parse_args()


def test_command(*, quiet: bool = False) -> list[str]:
    command = [
        str(BASE_DIR / "manage.py"),
        "test",
        *UI_PARTIAL_TESTS,
        "--noinput",
    ]
    if quiet:
        command.extend(["--verbosity", "0"])
    return command


def main(*, quiet: bool | None = None) -> None:
    args = parse_args() if quiet is None else argparse.Namespace(quiet=quiet)
    sys.path.insert(0, str(BASE_DIR))
    apply_defaults(LOCAL_DJANGO_DEFAULTS)
    command = test_command(quiet=args.quiet)
    if not args.quiet:
        print("\n".join(CHECK_DESCRIPTION))
    execute_from_command_line(command)


if __name__ == "__main__":
    main()
