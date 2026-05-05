from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import (
    AIRecommendation,
    AILearningProposal,
    FragranticaProduct,
)
from catalog.models import Brand, Perfume
from prices.services.catalog_review import build_catalogue_linking_rows


class CatalogueLinkingAIAdviceTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="catalogue-ai-staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(user)
        self.brand = Brand.objects.create(name="Antonio Banderas")
        self.perfume = Perfume.objects.create(
            brand=self.brand,
            name="The Icon",
            concentration="Eau de Parfum",
            collection_name="The Icon",
            audience="Men",
        )
        self.source = FragranticaProduct.objects.create(
            brand_name="Antonio Banderas",
            name="The Icon Eau de Parfum",
            collection_name="The Icon",
            audience="Men",
            release_year=2022,
        )

    def test_ai_advice_endpoint_requires_openai_settings(self):
        response = self.client.post(
            reverse("prices:catalogue_linking_ai_advice"),
            {"perfume": self.perfume.pk, "min_score": "80"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("OpenAI is not enabled", response.json()["error"])
        self.assertFalse(AIRecommendation.objects.exists())

    def test_ai_advice_endpoint_stores_review_only_recommendation(self):
        with (
            patch("prices.services.catalog_review.use_openai", return_value=True),
            patch(
                "assistant_linking.services.ai_advisor.use_openai", return_value=True
            ),
            patch(
                "assistant_linking.services.ai_advisor.create_structured_response",
                return_value={
                    "recommended_candidate_id": self.source.pk,
                    "confidence": 93,
                    "risk_level": "low",
                    "reasoning": "Fragrantica concentration text matches the local concentration.",
                    "candidate_notes": [
                        {
                            "candidate_id": self.source.pk,
                            "note": "Same brand and scent identity.",
                        }
                    ],
                },
            ),
        ):
            response = self.client.post(
                reverse("prices:catalogue_linking_ai_advice"),
                {"perfume": self.perfume.pk, "min_score": "80"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected"]["id"], self.perfume.pk)
        self.assertEqual(payload["ai_advice"]["confidence"], 93)
        self.assertEqual(
            payload["ai_advice"]["recommended_candidate_id"],
            self.source.pk,
        )

        recommendation = AIRecommendation.objects.get()
        self.assertEqual(recommendation.status, AIRecommendation.STATUS_PENDING)
        self.assertEqual(recommendation.fragrantica_product, self.source)
        self.assertEqual(recommendation.perfume, self.perfume)
        row_payload = json.loads(
            build_catalogue_linking_rows([self.perfume], min_score=80)[0][
                "payload_json"
            ]
        )
        self.assertEqual(row_payload["ai_advice"]["id"], recommendation.id)
        self.assertEqual(row_payload["ai_advice"]["confidence"], 93)

        self.source.refresh_from_db()
        self.assertEqual(self.source.match_status, FragranticaProduct.STATUS_UNLINKED)
        self.assertIsNone(self.source.matched_perfume_id)

    def test_ai_advice_review_accepts_recommendation_without_linking(self):
        recommendation = AIRecommendation.objects.create(
            task_type=AIRecommendation.TASK_FRAGRANTICA_LINK_RERANK,
            fragrantica_product=self.source,
            perfume=self.perfume,
            input_hash="a" * 64,
            prompt_version="ai-advisor-v1",
            model_name="test-model",
            confidence=91,
            risk_level=AIRecommendation.RISK_LOW,
            recommendation_json={
                "recommended_candidate_id": self.source.pk,
                "confidence": 91,
                "risk_level": "low",
                "reasoning": "Looks useful.",
                "candidate_notes": [],
            },
            reasoning="Looks useful.",
        )

        response = self.client.post(
            reverse(
                "prices:catalogue_linking_ai_advice_review", args=[recommendation.pk]
            ),
            {"action": "accept"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["ai_advice"]["status"], AIRecommendation.STATUS_ACCEPTED
        )
        self.assertFalse(payload["ai_advice"]["can_review"])
        self.assertEqual(
            payload["ai_advice"]["learning_proposal"]["status"],
            AILearningProposal.STATUS_PENDING,
        )

        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, AIRecommendation.STATUS_ACCEPTED)
        self.assertIsNotNone(recommendation.reviewed_by_id)
        self.assertIsNotNone(recommendation.reviewed_at)
        proposal = AILearningProposal.objects.get(source_recommendation=recommendation)
        self.assertEqual(proposal.status, AILearningProposal.STATUS_PENDING)
        self.assertEqual(proposal.proposed_action_json["perfume_id"], self.perfume.pk)
        self.assertEqual(
            proposal.proposed_action_json["fragrantica_product_id"],
            self.source.pk,
        )

        self.source.refresh_from_db()
        self.assertEqual(self.source.match_status, FragranticaProduct.STATUS_UNLINKED)
        self.assertIsNone(self.source.matched_perfume_id)

    def test_ai_advice_review_rejects_unknown_action(self):
        recommendation = AIRecommendation.objects.create(
            task_type=AIRecommendation.TASK_FRAGRANTICA_LINK_RERANK,
            fragrantica_product=self.source,
            perfume=self.perfume,
            input_hash="b" * 64,
            prompt_version="ai-advisor-v1",
        )

        response = self.client.post(
            reverse(
                "prices:catalogue_linking_ai_advice_review", args=[recommendation.pk]
            ),
            {"action": "maybe"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose accept or reject", response.json()["error"])
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, AIRecommendation.STATUS_PENDING)
