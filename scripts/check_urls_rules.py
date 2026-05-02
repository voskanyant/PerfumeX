#!/usr/bin/env python
"""Smoke tests for URL configuration checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_urls


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class UrlCheckerRuleTests(unittest.TestCase):
    def test_duplicate_route_names_are_reported(self):
        duplicates = check_urls.duplicate_route_names(
            [
                check_urls.NamedRoute(name="product_list", route="admin/products/"),
                check_urls.NamedRoute(name="product_list", route="products/"),
            ]
        )

        self.assertEqual(
            duplicates,
            {"product_list": ["admin/products/", "products/"]},
        )

    def test_allowed_duplicate_route_names_are_ignored(self):
        duplicates = check_urls.duplicate_route_names(
            [
                check_urls.NamedRoute(name="login", route="accounts/login/"),
                check_urls.NamedRoute(name="login", route="login/"),
            ],
            allowed_names={"login"},
        )

        self.assertEqual(duplicates, {})

    def test_namespaced_and_unnamespaced_names_do_not_collide(self):
        duplicates = check_urls.duplicate_route_names(
            [
                check_urls.NamedRoute(name="dashboard", route="admin/"),
                check_urls.NamedRoute(
                    name="assistant_core:dashboard",
                    route="admin/assistant/",
                ),
            ]
        )

        self.assertEqual(duplicates, {})


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UrlCheckerRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
