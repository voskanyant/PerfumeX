#!/usr/bin/env python
"""Smoke tests for local smoke runner coverage rules."""

from __future__ import annotations

import argparse
import unittest

import check_local_smoke
import check_make_targets


EXCLUDED_FROM_LOCAL_SMOKE = {
    "scripts/check_local_smoke.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


def smoke_step_scripts() -> set[str]:
    scripts: set[str] = set()
    for _label, args in check_local_smoke.SMOKE_STEPS:
        if args and args[0].endswith(".py"):
            scripts.add(args[0].replace("\\", "/"))
    return scripts


class LocalSmokeRuleTests(unittest.TestCase):
    def test_local_smoke_runs_focused_checker_targets(self):
        expected = {
            script_path
            for script_path in check_make_targets.EXPECTED_TARGETS.values()
            if script_path not in EXCLUDED_FROM_LOCAL_SMOKE
        }

        missing = sorted(expected - smoke_step_scripts())

        self.assertEqual(missing, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LocalSmokeRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
