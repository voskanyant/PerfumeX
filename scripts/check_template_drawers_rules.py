#!/usr/bin/env python
"""Smoke tests for template drawer/dialog checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_template_drawers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class TemplateDrawerRuleTests(unittest.TestCase):
    def test_valid_drawer_pattern_is_allowed(self):
        text = """
        <button type="button" data-drawer-toggle="filters" aria-controls="filtersDrawer" aria-expanded="false">Filters</button>
        <aside class="app-drawer" id="filtersDrawer" data-drawer="filters" aria-hidden="true" aria-labelledby="filtersTitle">
          <h2 id="filtersTitle">Filters</h2>
          <button type="button" data-drawer-close aria-label="Close"></button>
        </aside>
        """

        findings = check_template_drawers.template_drawer_findings(
            check_template_drawers.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_drawer_toggle_without_controls_is_reported(self):
        text = """
        <button type="button" data-drawer-toggle="filters" aria-expanded="false">Filters</button>
        <aside class="app-drawer" id="filtersDrawer" data-drawer="filters" aria-hidden="true" aria-label="Filters">
          <button type="button" data-drawer-close aria-label="Close"></button>
        </aside>
        """

        findings = check_template_drawers.template_drawer_findings(
            check_template_drawers.BASE_DIR / "example.html",
            text,
        )

        self.assertTrue(any("aria-controls" in finding.message for finding in findings))

    def test_drawer_without_accessible_close_is_reported(self):
        text = """
        <button type="button" data-drawer-toggle="filters" aria-controls="filtersDrawer" aria-expanded="false">Filters</button>
        <aside class="app-drawer" id="filtersDrawer" data-drawer="filters" aria-hidden="true" aria-label="Filters">
          <button type="button" data-drawer-close></button>
        </aside>
        """

        findings = check_template_drawers.template_drawer_findings(
            check_template_drawers.BASE_DIR / "example.html",
            text,
        )

        self.assertTrue(any("close" in finding.message for finding in findings))

    def test_dialog_without_label_is_reported(self):
        text = """
        <dialog class="modal">
          <h2>Shortcuts</h2>
        </dialog>
        """

        findings = check_template_drawers.template_drawer_findings(
            check_template_drawers.BASE_DIR / "example.html",
            text,
        )

        self.assertTrue(any("dialog" in finding.message for finding in findings))

    def test_dialog_with_labelledby_is_allowed(self):
        text = """
        <dialog class="modal" aria-labelledby="dialogTitle">
          <h2 id="dialogTitle">Shortcuts</h2>
        </dialog>
        """

        findings = check_template_drawers.template_drawer_findings(
            check_template_drawers.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TemplateDrawerRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
