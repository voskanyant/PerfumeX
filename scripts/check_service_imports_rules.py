#!/usr/bin/env python
"""Smoke tests for service import checker discovery rules."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import check_service_imports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class ServiceImportCheckerRuleTests(unittest.TestCase):
    def test_service_modules_discovers_local_app_services_in_stable_order(self):
        original_base_dir = check_service_imports.BASE_DIR
        original_local_apps = check_service_imports.LOCAL_APPS

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            services_dir = root / "alpha" / "services"
            services_dir.mkdir(parents=True)
            (services_dir / "__init__.py").write_text("", encoding="utf-8")
            (services_dir / "zeta.py").write_text("", encoding="utf-8")
            (services_dir / "alpha.py").write_text("", encoding="utf-8")

            try:
                check_service_imports.BASE_DIR = root
                check_service_imports.LOCAL_APPS = (
                    "missing_app",
                    "alpha",
                    "beta",
                )

                self.assertEqual(
                    check_service_imports.service_modules(),
                    [
                        "alpha.services.alpha",
                        "alpha.services.zeta",
                    ],
                )
            finally:
                check_service_imports.BASE_DIR = original_base_dir
                check_service_imports.LOCAL_APPS = original_local_apps


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        ServiceImportCheckerRuleTests
    )
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
