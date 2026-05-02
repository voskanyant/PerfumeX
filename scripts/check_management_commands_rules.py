#!/usr/bin/env python
"""Smoke tests for management command checker discovery rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import check_management_commands


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class ManagementCommandCheckerRuleTests(unittest.TestCase):
    def test_command_modules_discovers_local_app_commands_in_stable_order(self):
        original_base_dir = check_management_commands.BASE_DIR
        original_local_apps = check_management_commands.LOCAL_APPS

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands_dir = root / "alpha" / "management" / "commands"
            commands_dir.mkdir(parents=True)
            (commands_dir / "__init__.py").write_text("", encoding="utf-8")
            (commands_dir / "zeta.py").write_text("", encoding="utf-8")
            (commands_dir / "alpha.py").write_text("", encoding="utf-8")

            try:
                check_management_commands.BASE_DIR = root
                check_management_commands.LOCAL_APPS = (
                    "missing_app",
                    "alpha",
                    "beta",
                )

                self.assertEqual(
                    check_management_commands.command_modules(),
                    [
                        "alpha.management.commands.alpha",
                        "alpha.management.commands.zeta",
                    ],
                )
            finally:
                check_management_commands.BASE_DIR = original_base_dir
                check_management_commands.LOCAL_APPS = original_local_apps


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        ManagementCommandCheckerRuleTests
    )
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
