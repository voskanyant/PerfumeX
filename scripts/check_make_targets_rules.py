#!/usr/bin/env python
"""Smoke tests for Makefile target checking rules."""

from __future__ import annotations

import argparse
import unittest

import check_make_targets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


def makefile_text(
    *,
    omit_target: str | None = None,
    omit_phony: str | None = None,
    wrong_script_target: str | None = None,
) -> str:
    phony_targets = [
        target for target in check_make_targets.EXPECTED_TARGETS if target != omit_phony
    ]
    lines = [".PHONY: " + " ".join(sorted(phony_targets)), ""]
    lines.extend(["export POSTGRES_PASSWORD ?=", ""])

    for target, script_path in sorted(check_make_targets.EXPECTED_TARGETS.items()):
        if target == omit_target:
            continue
        command_script = (
            "scripts/not_the_expected_script.py"
            if target == wrong_script_target
            else script_path
        )
        lines.extend([f"{target}:", f"\t$(PYTHON) {command_script}", ""])

    return "\n".join(lines)


class MakeTargetRuleTests(unittest.TestCase):
    def test_valid_makefile_passes(self):
        failures = check_make_targets.check_targets(makefile_text())

        self.assertEqual(failures, [])

    def test_missing_target_is_reported(self):
        failures = check_make_targets.check_targets(
            makefile_text(omit_target="doc-drift")
        )

        self.assertIn("missing Makefile target: doc-drift", failures)

    def test_missing_phony_entry_is_reported(self):
        failures = check_make_targets.check_targets(
            makefile_text(omit_phony="js-smoke")
        )

        self.assertIn("Makefile target is not listed in .PHONY: js-smoke", failures)

    def test_non_checker_target_missing_phony_entry_is_reported(self):
        content = makefile_text() + "\ncustom:\n\t$(PYTHON) manage.py check\n"

        failures = check_make_targets.check_targets(content)

        self.assertIn("Makefile target is not listed in .PHONY: custom", failures)

    def test_stale_phony_entry_is_reported(self):
        content = makefile_text().replace(".PHONY: ", ".PHONY: custom ", 1)

        failures = check_make_targets.check_targets(content)

        self.assertIn(
            ".PHONY target has no matching Makefile target: custom",
            failures,
        )

    def test_wrong_script_is_reported(self):
        failures = check_make_targets.check_targets(
            makefile_text(wrong_script_target="template-smoke")
        )

        self.assertIn(
            "Makefile target 'template-smoke' does not run expected script: "
            "scripts/check_templates.py",
            failures,
        )

    def test_bare_python_recipe_is_reported(self):
        content = makefile_text().replace(
            "\t$(PYTHON) scripts/check_css_static.py",
            "\tpython scripts/check_css_static.py",
        )

        failures = check_make_targets.check_targets(content)

        self.assertIn(
            "Makefile target 'css-smoke' should use $(PYTHON), not bare python",
            failures,
        )

    def test_bare_python_recipe_is_reported_for_non_checker_target(self):
        content = makefile_text() + "\ncustom:\n\tpython manage.py check\n"

        failures = check_make_targets.check_targets(content)

        self.assertIn(
            "Makefile target 'custom' should use $(PYTHON), not bare python",
            failures,
        )

    def test_unmapped_check_script_is_reported(self):
        failures = check_make_targets.check_script_target_coverage(
            expected_targets={"known-target": "scripts/check_known.py"},
            discovered_scripts={
                "scripts/check_known.py",
                "scripts/check_new_guard.py",
            },
        )

        self.assertIn(
            "missing Makefile target mapping for check script: "
            "scripts/check_new_guard.py",
            failures,
        )

    def test_stale_target_script_is_reported(self):
        failures = check_make_targets.check_script_target_coverage(
            expected_targets={
                "known-target": "scripts/check_known.py",
                "stale-target": "scripts/check_deleted.py",
            },
            discovered_scripts={"scripts/check_known.py"},
        )

        self.assertIn(
            "expected Makefile target script does not exist: "
            "scripts/check_deleted.py",
            failures,
        )

    def test_checker_without_rule_script_is_reported(self):
        failures = check_make_targets.check_rule_pair_coverage(
            {
                "scripts/check_known.py",
                "scripts/check_known_rules.py",
                "scripts/check_new_guard.py",
            }
        )

        self.assertIn(
            "checker script has no matching rule script: scripts/check_new_guard.py",
            failures,
        )

    def test_rule_without_checker_script_is_reported(self):
        failures = check_make_targets.check_rule_pair_coverage(
            {
                "scripts/check_known.py",
                "scripts/check_known_rules.py",
                "scripts/check_deleted_rules.py",
            }
        )

        self.assertIn(
            "rule script has no matching checker script: "
            "scripts/check_deleted_rules.py",
            failures,
        )


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MakeTargetRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
