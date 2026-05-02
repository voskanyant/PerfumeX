from __future__ import annotations

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from assistant_linking.services.suggestions import generate_suggestions_for_product


class SuggestionsServiceTests(SimpleTestCase):
    def test_generate_suggestions_for_product_reports_count_and_message(self):
        generator = MagicMock(
            return_value=[
                {"id": 1, "confidence": 90},
                {"id": 2, "confidence": 80},
            ]
        )

        result = generate_suggestions_for_product(
            12,
            generator=generator,
        )

        generator.assert_called_once_with(12)
        self.assertEqual(result.count, 2)
        self.assertEqual(result.message, "Generated 2 suggestions.")
        self.assertEqual(
            result.suggestions,
            [
                {"id": 1, "confidence": 90},
                {"id": 2, "confidence": 80},
            ],
        )

    def test_generate_suggestions_for_product_handles_empty_results(self):
        result = generate_suggestions_for_product(
            12,
            generator=MagicMock(return_value=[]),
        )

        self.assertEqual(result.count, 0)
        self.assertEqual(result.message, "Generated 0 suggestions.")
