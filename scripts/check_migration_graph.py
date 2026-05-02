#!/usr/bin/env python
"""Check Django migration graph consistency without a database connection."""

from __future__ import annotations

import argparse
import sys

from smoke_env import BASE_DIR, LOCAL_DJANGO_DEFAULTS, apply_defaults


CHECK_DESCRIPTION = """\
Migration graph smoke check:
- loads migrations from disk only.
- detects conflicting leaf migrations.
- lets Django raise on missing dependencies or graph cycles.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def load_migration_loader():
    apply_defaults(LOCAL_DJANGO_DEFAULTS)
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    import django
    from django.db.migrations.loader import MigrationLoader

    django.setup()
    return MigrationLoader(
        None,
        ignore_no_migrations=True,
        replace_migrations=True,
    )


def conflict_messages(conflicts: dict[str, list[str]]) -> list[str]:
    messages: list[str] = []
    for app_label, migration_names in sorted(conflicts.items()):
        names = ", ".join(migration_names)
        messages.append(f"- {app_label}: {names}")
    return messages


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)

    try:
        loader = load_migration_loader()
    except Exception as exc:
        print(f"Migration graph check failed while loading migrations: {exc}")
        return 1

    conflicts = loader.detect_conflicts()
    if conflicts:
        print("\nMigration graph conflicts detected:")
        for message in conflict_messages(conflicts):
            print(message)
        return 1

    if not quiet:
        print("- no migration conflicts detected")
        print("- migration dependencies resolved")
    print(f"\nMigration graph check passed for {len(loader.disk_migrations)} migration(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
