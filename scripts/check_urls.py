#!/usr/bin/env python
"""Smoke-check local URL configuration shape."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass

from smoke_env import BASE_DIR, LOCAL_DJANGO_DEFAULTS, apply_defaults


CHECK_DESCRIPTION = """\
URL configuration smoke check:
- initializes Django with local-safe defaults.
- loads the root URL resolver.
- checks for new duplicate un-namespaced route names.
"""

# Django auth URLs are included for password flows, while root login/logout
# routes intentionally override the visible auth entry points.
ALLOWED_DUPLICATE_NAMES = {"login", "logout"}


@dataclass(frozen=True)
class NamedRoute:
    name: str
    route: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary and any failures.",
    )
    return parser.parse_args()


def collect_named_routes(patterns, *, namespace: tuple[str, ...] = (), prefix: str = "") -> list[NamedRoute]:
    from django.urls import URLPattern, URLResolver

    routes: list[NamedRoute] = []
    for pattern in patterns:
        route = f"{prefix}{pattern.pattern}"
        if isinstance(pattern, URLPattern):
            if pattern.name:
                name = ":".join((*namespace, pattern.name)) if namespace else pattern.name
                routes.append(NamedRoute(name=name, route=route))
            continue

        if isinstance(pattern, URLResolver):
            child_namespace = namespace
            if pattern.namespace:
                child_namespace = (*namespace, pattern.namespace)
            routes.extend(
                collect_named_routes(
                    pattern.url_patterns,
                    namespace=child_namespace,
                    prefix=route,
                )
            )
    return routes


def duplicate_route_names(
    routes: list[NamedRoute],
    *,
    allowed_names: set[str] | None = None,
) -> dict[str, list[str]]:
    allowed_names = allowed_names or set()
    by_name: dict[str, list[str]] = defaultdict(list)
    for route in routes:
        by_name[route.name].append(route.route)
    return {
        name: route_values
        for name, route_values in sorted(by_name.items())
        if len(route_values) > 1 and name not in allowed_names
    }


def main(*, quiet: bool = False) -> int:
    if not quiet:
        print(CHECK_DESCRIPTION, flush=True)
    sys.path.insert(0, str(BASE_DIR))
    apply_defaults(LOCAL_DJANGO_DEFAULTS)

    import django
    from django.urls import get_resolver

    django.setup()
    resolver = get_resolver()
    routes = collect_named_routes(resolver.url_patterns)
    duplicates = duplicate_route_names(routes, allowed_names=ALLOWED_DUPLICATE_NAMES)

    if duplicates:
        print("\nDuplicate URL names found:")
        for name, route_values in duplicates.items():
            print(f"- {name}: {', '.join(route_values)}")
        return 1

    print(f"\nURL configuration check passed for {len(routes)} named route(s).")
    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(quiet=args.quiet))
