#!/usr/bin/env python
"""Smoke tests for view export checker wrapper rules."""

from __future__ import annotations

import argparse
import unittest

import check_view_exports


EXPECTED_VIEW_EXPORT_TESTS = [
    "prices.test_view_exports",
    "assistant_core.tests.test_view_exports",
    "assistant_linking.tests.test_view_exports",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class ViewExportCheckerRuleTests(unittest.TestCase):
    def test_view_export_checker_runs_all_app_boundary_suites(self):
        self.assertEqual(check_view_exports.VIEW_EXPORT_TESTS, EXPECTED_VIEW_EXPORT_TESTS)

    def test_quiet_command_uses_django_test_runner_and_low_verbosity(self):
        command = check_view_exports.test_command(quiet=True)

        self.assertEqual(command[1], "test")
        self.assertEqual(command[2:5], EXPECTED_VIEW_EXPORT_TESTS)
        self.assertIn("--noinput", command)
        self.assertEqual(command[-2:], ["--verbosity", "0"])

    def test_non_quiet_command_keeps_default_verbosity(self):
        command = check_view_exports.test_command(quiet=False)

        self.assertEqual(command[1], "test")
        self.assertEqual(command[2:5], EXPECTED_VIEW_EXPORT_TESTS)
        self.assertIn("--noinput", command)
        self.assertNotIn("--verbosity", command)


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ViewExportCheckerRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
