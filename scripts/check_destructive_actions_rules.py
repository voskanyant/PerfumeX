#!/usr/bin/env python
"""Smoke tests for destructive action checker rules."""

from __future__ import annotations

import argparse
import unittest

import check_destructive_actions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class DestructiveActionRuleTests(unittest.TestCase):
    def test_danger_submit_without_confirm_is_reported(self):
        text = '<form method="post"><button class="button danger" type="submit">Archive</button></form>'

        findings = check_destructive_actions.destructive_action_findings(
            check_destructive_actions.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)

    def test_delete_submit_without_confirm_is_reported(self):
        text = '<form method="post"><button class="button ghost" type="submit">Delete</button></form>'

        findings = check_destructive_actions.destructive_action_findings(
            check_destructive_actions.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)

    def test_confirm_on_submit_control_is_allowed(self):
        text = (
            '<form method="post"><button class="button danger" type="submit" '
            'data-confirm="Delete?">Delete</button></form>'
        )

        findings = check_destructive_actions.destructive_action_findings(
            check_destructive_actions.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_confirm_on_form_is_allowed(self):
        text = '<form method="post" data-confirm="Delete?"><button type="submit">Delete</button></form>'

        findings = check_destructive_actions.destructive_action_findings(
            check_destructive_actions.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_delete_confirmation_link_with_danger_style_is_allowed(self):
        text = '<a class="button danger" href="/delete/1/">Delete</a>'

        findings = check_destructive_actions.destructive_action_findings(
            check_destructive_actions.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])

    def test_delete_confirmation_link_without_danger_style_is_reported(self):
        text = '<a class="button ghost" href="/delete/1/">Delete</a>'

        findings = check_destructive_actions.destructive_action_findings(
            check_destructive_actions.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("danger", findings[0].message)

    def test_cancel_link_is_allowed(self):
        text = '<a class="button ghost" href="/products/">Cancel</a>'

        findings = check_destructive_actions.destructive_action_findings(
            check_destructive_actions.BASE_DIR / "example.html",
            text,
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DestructiveActionRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
