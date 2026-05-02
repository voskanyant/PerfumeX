#!/usr/bin/env python
"""Compile all local Django templates without rendering them."""

from __future__ import annotations

import argparse
import sys

from smoke_env import BASE_DIR, LOCAL_APPS, LOCAL_DJANGO_DEFAULTS, apply_defaults

CHECK_DESCRIPTION = """\
Django template compile smoke check:
- discovers local app templates.
- compiles each template through Django's template loader.
- catches template syntax and tag-library errors without rendering pages.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def template_names() -> list[str]:
    names: set[str] = set()
    for app_label in LOCAL_APPS:
        template_root = BASE_DIR / app_label / "templates"
        if not template_root.exists():
            continue
        for path in template_root.rglob("*.html"):
            names.add(path.relative_to(template_root).as_posix())
    return sorted(names)


def main() -> int:
    args = parse_args()
    if not args.quiet:
        print(CHECK_DESCRIPTION, flush=True)
    sys.path.insert(0, str(BASE_DIR))
    apply_defaults(LOCAL_DJANGO_DEFAULTS)

    import django
    from django.template import engines

    django.setup()
    engine = engines["django"]

    names = template_names()
    if not names:
        print("No local Django templates found.")
        return 0

    failures: list[tuple[str, Exception]] = []
    for name in names:
        if not args.quiet:
            print(f"Compiling {name}", flush=True)
        try:
            engine.get_template(name)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - smoke check reports any compile failure.
            failures.append((name, exc))

    if failures:
        print("\nDjango template compile check failed:")
        for name, exc in failures:
            print(f"- {name}: {exc.__class__.__name__}: {exc}")
        return 1

    print(f"\nDjango template compile check passed for {len(names)} template(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
