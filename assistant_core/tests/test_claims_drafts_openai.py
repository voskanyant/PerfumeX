import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from assistant_core.services.mock_description_generator import create_mock_draft
from assistant_core.services.openai_draft_writer import create_openai_draft
from catalog.models import AIDraft, Brand, FactClaim, Perfume, Source


class ClaimsDraftTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Example Brand")
        self.perfume = Perfume.objects.create(brand=self.brand, name="Example Perfume")
        self.source = Source.objects.create(
            perfume=self.perfume,
            url="https://example.com/official",
            source_type="official_brand",
            source_domain="example.com",
        )

    def test_mock_draft_uses_only_approved_claims_and_stays_pending(self):
        approved = FactClaim.objects.create(
            perfume=self.perfume,
            source=self.source,
            field_name="top_notes",
            value_json=["bergamot"],
            confidence="high",
            status=FactClaim.STATUS_APPROVED,
            claim_hash="a",
        )
        FactClaim.objects.create(
            perfume=self.perfume,
            source=self.source,
            field_name="base_notes",
            value_json=["musk"],
            status=FactClaim.STATUS_REJECTED,
            claim_hash="b",
        )

        draft = create_mock_draft(self.perfume.id)

        self.assertEqual(draft.status, AIDraft.STATUS_PENDING)
        self.assertEqual(draft.source_claims_json, [approved.id])
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.summary_short, "")

    @override_settings(ASSISTANT_USE_OPENAI=True, OPENAI_MODEL_WRITER="gpt-test")
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test"})
    @patch("assistant_core.services.openai_responses.OpenAI")
    def test_openai_draft_writer_uses_mocked_response(self, mock_openai_cls):
        FactClaim.objects.create(
            perfume=self.perfume,
            source=self.source,
            field_name="top_notes",
            value_json=["bergamot"],
            confidence="high",
            status=FactClaim.STATUS_APPROVED,
            claim_hash="claim-1",
        )
        payload = {
            "short_description": "Bright citrus opening.",
            "long_description": "A bright citrus opening with a clean woody base.",
            "beginner_description": "Fresh and easy to understand.",
            "seo_title": "Example Perfume review draft",
            "seo_description": "Draft text from approved facts only.",
            "mood_tags": ["fresh", "clean"],
            "warnings": [],
        }
        mock_openai_cls.return_value.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload))

        draft = create_openai_draft(self.perfume.id)

        self.assertEqual(draft.status, AIDraft.STATUS_PENDING)
        self.assertEqual(draft.content_json["short_description"], "Bright citrus opening.")
