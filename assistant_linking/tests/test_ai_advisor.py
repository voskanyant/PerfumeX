from __future__ import annotations

from django.test import SimpleTestCase, TestCase
from types import SimpleNamespace

from assistant_linking.models import AIRecommendation, FragranticaProduct
from assistant_linking.services.ai_advisor import (
    build_fragrantica_rerank_context,
    create_fragrantica_rerank_recommendation,
    stable_input_hash,
    validate_fragrantica_rerank_payload,
)
from catalog.models import Brand, Perfume


class AIAdvisorHashTests(SimpleTestCase):
    def test_stable_input_hash_ignores_dict_key_order(self):
        first = {"local": {"name": "The Icon", "brand": "Antonio Banderas"}}
        second = {"local": {"brand": "Antonio Banderas", "name": "The Icon"}}

        self.assertEqual(stable_input_hash(first), stable_input_hash(second))

    def test_validate_fragrantica_payload_rejects_unknown_candidate_id(self):
        with self.assertRaisesMessage(
            ValueError,
            "AI recommended an unknown Fragrantica candidate ID.",
        ):
            validate_fragrantica_rerank_payload(
                {
                    "recommended_candidate_id": 99,
                    "confidence": 90,
                    "risk_level": "low",
                    "reasoning": "Looks correct.",
                    "candidate_notes": [],
                },
                candidate_ids={1},
            )

    def test_context_accepts_match_candidate_wrapper(self):
        perfume = SimpleNamespace(
            id=10,
            brand=SimpleNamespace(name="Antonio Banderas"),
            name="The Icon",
            concentration="Eau de Parfum",
            collection_name="The Icon",
            audience="Men",
            release_year=2022,
        )
        source = SimpleNamespace(
            id=20,
            brand_name="Antonio Banderas",
            name="The Icon Eau de Parfum",
            collection_name="The Icon",
            audience="Men",
            release_year=2022,
            matched_perfume_id=None,
        )
        candidate = SimpleNamespace(
            source=source,
            score=100,
            reason="Exact brand, scent, and concentration match",
        )

        context = build_fragrantica_rerank_context(
            perfume=perfume,
            candidates=[candidate],
        )

        self.assertEqual(context["candidates"][0]["id"], 20)
        self.assertEqual(context["candidates"][0]["deterministic_score"], 100)
        self.assertEqual(
            context["candidates"][0]["deterministic_reason"],
            "Exact brand, scent, and concentration match",
        )


class AIAdvisorRecommendationTests(TestCase):
    def test_creates_pending_fragrantica_recommendation_without_link_mutation(self):
        brand = Brand.objects.create(name="Antonio Banderas")
        perfume = Perfume.objects.create(
            brand=brand,
            name="The Icon",
            concentration="Eau de Parfum",
            collection_name="The Icon",
        )
        fragrantica_product = FragranticaProduct.objects.create(
            brand_name="Antonio Banderas",
            name="The Icon Eau de Parfum",
            collection_name="The Icon",
            audience="Men",
            release_year=2022,
        )

        recommendation = create_fragrantica_rerank_recommendation(
            perfume=perfume,
            candidates=[fragrantica_product],
            payload={
                "recommended_candidate_id": fragrantica_product.id,
                "confidence": 94,
                "risk_level": "low",
                "reasoning": "Fragrantica name carries the same concentration.",
                "candidate_notes": [
                    {
                        "candidate_id": fragrantica_product.id,
                        "note": "Concentration text matches local concentration.",
                    }
                ],
            },
        )

        self.assertEqual(
            recommendation.task_type,
            AIRecommendation.TASK_FRAGRANTICA_LINK_RERANK,
        )
        self.assertEqual(recommendation.status, AIRecommendation.STATUS_PENDING)
        self.assertEqual(recommendation.confidence, 94)
        self.assertEqual(recommendation.fragrantica_product, fragrantica_product)
        self.assertEqual(recommendation.perfume, perfume)
        self.assertTrue(recommendation.input_hash)
        self.assertEqual(
            recommendation.recommendation_json["recommended_candidate_id"],
            fragrantica_product.id,
        )

        fragrantica_product.refresh_from_db()
        perfume.refresh_from_db()
        self.assertEqual(
            fragrantica_product.match_status, FragranticaProduct.STATUS_UNLINKED
        )
        self.assertIsNone(fragrantica_product.matched_perfume_id)
        self.assertEqual(perfume.name, "The Icon")
