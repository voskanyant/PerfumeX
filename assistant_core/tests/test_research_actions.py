from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from assistant_core.services.research_actions import (
    generate_mock_draft_action,
    run_mock_brand_watch_action,
)


class ResearchActionServiceTests(SimpleTestCase):
    def test_run_mock_brand_watch_action_returns_job_summary(self):
        calls = []

        def runner(profile_id):
            calls.append(profile_id)
            return SimpleNamespace(result_summary="Mock scan created 2 changes.")

        action = run_mock_brand_watch_action(7, runner=runner)

        self.assertEqual(calls, [7])
        self.assertEqual(action.message, "Mock scan created 2 changes.")
        self.assertEqual(action.payload.result_summary, "Mock scan created 2 changes.")

    def test_generate_mock_draft_action_returns_standard_message(self):
        calls = []
        draft = SimpleNamespace(id=11)

        def runner(perfume_id):
            calls.append(perfume_id)
            return draft

        action = generate_mock_draft_action(13, runner=runner)

        self.assertEqual(calls, [13])
        self.assertEqual(
            action.message, "Pending draft generated from approved claims."
        )
        self.assertIs(action.payload, draft)
