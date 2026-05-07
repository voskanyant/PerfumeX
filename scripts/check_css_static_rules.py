#!/usr/bin/env python
"""Smoke tests for CSS rule checker behavior."""

from __future__ import annotations

import argparse
import unittest

import check_css_static


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class CssRuleCheckerTests(unittest.TestCase):
    def test_balanced_css_is_allowed(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            ".title { letter-spacing: 0; font-size: 16px; }\n",
        )

        self.assertEqual(findings, [])

    def test_negative_letter_spacing_is_reported(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            ".title { letter-spacing: -0.02em; }\n",
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("negative letter-spacing", findings[0].message)

    def test_viewport_font_size_is_reported(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            ".title { font-size: 4vw; }\n",
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("viewport-scaled", findings[0].message)

    def test_desktop_action_controls_may_stay_compact(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            ".button.icon, .drawer-close { width: 32px; height: 32px; }\n",
        )

        self.assertEqual(findings, [])

    def test_mobile_action_control_touch_targets_are_reported(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            "@media (max-width: 767.98px) {\n"
            "  .button.icon, .search-clear-text { width: 40px; height: 42px; }\n"
            "}\n",
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("mobile width", findings[0].message)

    def test_mobile_action_control_touch_targets_allow_42px(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            "@media (max-width: 767.98px) {\n"
            "  .button.icon, .search-clear-text { width: 42px; min-width: 42px; height: 42px; }\n"
            "}\n",
        )

        self.assertEqual(findings, [])

    def test_mobile_action_control_min_height_is_reported(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            "@media (max-width: 767.98px) {\n"
            "  .catalogue-linking-option { min-height: 40px; }\n"
            "}\n",
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("mobile min-height", findings[0].message)

    def test_unbalanced_brace_is_reported(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            ".title { color: black;\n",
        )

        self.assertEqual(len(findings), 1)
        self.assertIn("unmatched opening brace", findings[0].message)

    def test_braces_inside_comments_are_ignored(self):
        findings = check_css_static.css_rule_findings(
            check_css_static.BASE_DIR / "example.css",
            "/* ignored { */\n.title { color: black; }\n",
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CssRuleCheckerTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
