#!/usr/bin/env python
"""Smoke tests for secret-pattern checking rules."""

from __future__ import annotations

import argparse
import unittest

import check_secret_patterns


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print unittest failures.",
    )
    return parser.parse_args()


class SecretPatternRuleTests(unittest.TestCase):
    def reasons_for(self, text: str) -> list[str]:
        return [
            finding.reason
            for finding in check_secret_patterns.find_secret_findings("example.txt", text)
        ]

    def test_private_key_block_is_reported(self):
        reasons = self.reasons_for("-----BEGIN OPENSSH PRIVATE KEY-----")

        self.assertIn("private key block", reasons)

    def test_credentialed_url_is_reported(self):
        reasons = self.reasons_for("DATABASE_URL=postgres://user:pass@example/db")

        self.assertIn("credentialed URL", reasons)

    def test_openai_style_token_is_reported(self):
        reasons = self.reasons_for("token = sk-1234567890abcdef1234567890abcdef")

        self.assertIn("token value", reasons)

    def test_non_placeholder_assignment_is_reported(self):
        reasons = self.reasons_for("PASSWORD=actualProductionPassword")

        self.assertIn("secret assignment: PASSWORD", reasons)

    def test_yaml_style_assignment_is_reported(self):
        reasons = self.reasons_for("password: actualProductionPassword")

        self.assertIn("secret assignment: password", reasons)

    def test_json_style_assignment_is_reported(self):
        reasons = self.reasons_for('{"api_key": "actualProductionKey"}')

        self.assertIn("secret assignment: api_key", reasons)

    def test_placeholder_assignment_is_allowed(self):
        reasons = self.reasons_for("SECRET_KEY=local-not-secret-for-tests")

        self.assertEqual(reasons, [])

    def test_environment_reference_assignment_is_allowed(self):
        reasons = self.reasons_for("SECRET_KEY=os.getenv('SECRET_KEY')")

        self.assertEqual(reasons, [])

    def test_type_annotation_is_allowed(self):
        reasons = self.reasons_for("token: str")

        self.assertEqual(reasons, [])

    def test_django_widget_reference_is_allowed(self):
        reasons = self.reasons_for('widgets = {"password": forms.PasswordInput()}')

        self.assertEqual(reasons, [])

    def test_generic_security_docs_do_not_trigger_assignment_check(self):
        reasons = self.reasons_for("Do not store secrets, passwords, or private credentials.")

        self.assertEqual(reasons, [])


if __name__ == "__main__":
    args = parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SecretPatternRuleTests)
    runner = unittest.TextTestRunner(verbosity=0 if args.quiet else 1)
    raise SystemExit(0 if runner.run(suite).wasSuccessful() else 1)
