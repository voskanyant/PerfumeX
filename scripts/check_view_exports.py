from __future__ import annotations

import argparse
import sys

from django.core.management import execute_from_command_line

from smoke_env import BASE_DIR, LOCAL_DJANGO_DEFAULTS, apply_defaults

VIEW_EXPORT_TESTS = [
    "prices.test_view_exports",
    "assistant_core.tests.test_view_exports",
    "assistant_linking.tests.test_view_exports",
]
CHECK_DESCRIPTION = [
    "View export boundary check:",
    "- app-level views.py modules must stay import-only compatibility layers.",
    "- compatibility modules may import focused views_*.py modules only, except approved mixins.",
    "- focused views_*.py modules must not grow top-level helper functions unless allowlisted.",
    "- reusable view helpers belong in the owning services/ module.",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run view export boundary checks.")
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
        *VIEW_EXPORT_TESTS,
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
