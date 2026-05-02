#!/usr/bin/env python
"""Smoke tests for migration graph checking rules."""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import check_migration_graph


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class FakeMigrationLoader:
    def __init__(self, conflicts: dict[str, list[str]] | None = None, count: int = 2):
        self._conflicts = conflicts or {}
        self.disk_migrations = {
            ("prices", f"{index:04d}_migration"): object()
            for index in range(1, count + 1)
        }

    def detect_conflicts(self) -> dict[str, list[str]]:
        return self._conflicts


class MigrationGraphRuleTests(unittest.TestCase):
    def run_main_with_loader(self, loader: FakeMigrationLoader | Exception) -> tuple[int, str]:
        buffer = io.StringIO()
        if isinstance(loader, Exception):
            side_effect = loader
            return_value = None
        else:
            side_effect = None
            return_value = loader

        with patch.object(
            check_migration_graph,
            "load_migration_loader",
            return_value=return_value,
            side_effect=side_effect,
        ):
            with redirect_stdout(buffer):
                result = check_migration_graph.main(quiet=True)
        return result, buffer.getvalue()

    def test_no_conflicts_passes(self):
        result, output = self.run_main_with_loader(FakeMigrationLoader())

        self.assertEqual(result, 0)
        self.assertIn("Migration graph check passed for 2 migration(s).", output)

    def test_conflicts_fail(self):
        result, output = self.run_main_with_loader(
            FakeMigrationLoader({"prices": ["0002_a", "0002_b"]})
        )

        self.assertEqual(result, 1)
        self.assertIn("Migration graph conflicts detected", output)
        self.assertIn("- prices: 0002_a, 0002_b", output)

    def test_loader_failure_fails(self):
        result, output = self.run_main_with_loader(RuntimeError("missing dependency"))

        self.assertEqual(result, 1)
        self.assertIn(
            "Migration graph check failed while loading migrations: missing dependency",
            output,
        )

    def test_conflict_messages_are_sorted_by_app(self):
        messages = check_migration_graph.conflict_messages(
            {
                "prices": ["0003_x", "0003_y"],
                "catalog": ["0002_x", "0002_y"],
            }
        )

        self.assertEqual(
            messages,
            [
                "- catalog: 0002_x, 0002_y",
                "- prices: 0003_x, 0003_y",
            ],
        )


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MigrationGraphRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
