#!/usr/bin/env python
"""Smoke tests for UI partial checker wrapper rules."""

from __future__ import annotations

import argparse
import unittest

import check_ui_partials


EXPECTED_UI_PARTIAL_TESTS = [
    "prices.test_ui_partials",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class UiPartialCheckerRuleTests(unittest.TestCase):
    def test_ui_partial_checker_runs_shared_partial_boundary_suite(self):
        self.assertEqual(check_ui_partials.UI_PARTIAL_TESTS, EXPECTED_UI_PARTIAL_TESTS)

    def test_quiet_command_uses_django_test_runner_and_low_verbosity(self):
        command = check_ui_partials.test_command(quiet=True)

        self.assertEqual(command[1], "test")
        self.assertEqual(command[2:3], EXPECTED_UI_PARTIAL_TESTS)
        self.assertIn("--noinput", command)
        self.assertEqual(command[-2:], ["--verbosity", "0"])

    def test_non_quiet_command_keeps_default_verbosity(self):
        command = check_ui_partials.test_command(quiet=False)

        self.assertEqual(command[1], "test")
        self.assertEqual(command[2:3], EXPECTED_UI_PARTIAL_TESTS)
        self.assertIn("--noinput", command)
        self.assertNotIn("--verbosity", command)


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UiPartialCheckerRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
