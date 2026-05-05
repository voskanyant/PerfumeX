from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import (
    AIRecommendation,
    AILearningProposal,
    BrandAlias,
    FragranticaProduct,
    FragranticaProductLink,
    ManualLinkDecision,
    ParsedSupplierProduct,
    ProductAlias,
)
from catalog.models import Brand, Perfume
from prices.models import Supplier, SupplierProduct


class AIRecommendationQueueTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="ai-queue-staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(user)
        brand = Brand.objects.create(name="Antonio Banderas")
        self.perfume = Perfume.objects.create(
            brand=brand,
            name="The Icon",
            concentration="Eau de Parfum",
        )
        self.source = FragranticaProduct.objects.create(
            brand_name="Antonio Banderas",
            name="The Icon Eau de Parfum",
        )

    def create_recommendation(
        self,
        *,
        status=AIRecommendation.STATUS_PENDING,
        reasoning="Fragrantica candidate looks useful.",
    ):
        return AIRecommendation.objects.create(
            task_type=AIRecommendation.TASK_FRAGRANTICA_LINK_RERANK,
            status=status,
            fragrantica_product=self.source,
            perfume=self.perfume,
            input_hash="c" * 64,
            prompt_version="ai-advisor-v1",
            model_name="test-model",
            confidence=91,
            risk_level=AIRecommendation.RISK_LOW,
            reasoning=reasoning,
        )

    def test_queue_lists_pending_recommendations_by_default(self):
        self.create_recommendation(status=AIRecommendation.STATUS_PENDING)
        self.create_recommendation(status=AIRecommendation.STATUS_REJECTED)

        response = self.client.get(reverse("assistant_linking:ai_recommendation_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI recommendations")
        self.assertContains(response, "Fragrantica link rerank")
        self.assertContains(response, "Fragrantica candidate looks useful.", count=1)
        recommendation = AIRecommendation.objects.get(
            status=AIRecommendation.STATUS_PENDING
        )
        self.assertContains(
            response,
            reverse(
                "assistant_linking:ai_recommendation_detail",
                args=[recommendation.pk],
            ),
        )
        self.assertContains(
            response,
            reverse("prices:catalogue_linking_workbench")
            + f"?perfume={self.perfume.pk}",
        )

    def test_detail_page_shows_proposal_evidence_and_actions(self):
        recommendation = self.create_recommendation()
        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[recommendation.pk],
            ),
            {"action": "accept"},
        )

        response = self.client.get(
            reverse(
                "assistant_linking:ai_recommendation_detail",
                args=[recommendation.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI recommendation")
        self.assertContains(response, "Fragrantica link review")
        self.assertContains(response, "Fragrantica candidate looks useful.")
        self.assertContains(response, "Review checklist")
        self.assertContains(response, "Recommendation review")
        self.assertContains(response, "Manual link")
        self.assertContains(response, "Apply proposal")
        self.assertContains(response, "Open target")

    def test_queue_can_filter_all_statuses(self):
        self.create_recommendation(status=AIRecommendation.STATUS_PENDING)
        self.create_recommendation(status=AIRecommendation.STATUS_REJECTED)

        response = self.client.get(
            reverse("assistant_linking:ai_recommendation_queue"),
            {"status": "all"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fragrantica candidate looks useful.", count=2)

    def test_queue_can_filter_pending_learning_proposals(self):
        with_proposal = self.create_recommendation(reasoning="Accepted proposal row.")
        self.create_recommendation(reasoning="Still pending advice.")
        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[with_proposal.pk],
            ),
            {"action": "accept"},
        )

        response = self.client.get(
            reverse("assistant_linking:ai_recommendation_queue"),
            {"status": "all", "proposal": AILearningProposal.STATUS_PENDING},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accepted proposal row.")
        self.assertContains(response, "Proposal Pending")
        self.assertNotContains(response, "Still pending advice.")

    def test_queue_can_filter_ready_to_apply_workflow(self):
        with_proposal = self.create_recommendation(reasoning="Ready proposal row.")
        self.create_recommendation(
            status=AIRecommendation.STATUS_ACCEPTED,
            reasoning="Accepted without proposal.",
        )
        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[with_proposal.pk],
            ),
            {"action": "accept"},
        )

        response = self.client.get(
            reverse("assistant_linking:ai_recommendation_queue"),
            {"status": "all", "workflow": "ready_apply"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ready proposal row.")
        self.assertContains(response, "Ready to apply")
        self.assertNotContains(response, "Accepted without proposal.")

    def test_queue_can_filter_by_proposal_type(self):
        with_proposal = self.create_recommendation(reasoning="Fragrantica proposal.")
        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[with_proposal.pk],
            ),
            {"action": "accept"},
        )
        product_alias_recommendation = AIRecommendation.objects.create(
            task_type=AIRecommendation.TASK_KB_SUGGESTION,
            perfume=self.perfume,
            input_hash="p" * 64,
            prompt_version="manual-pattern-v1",
            model_name="deterministic-pattern-v1",
            confidence=88,
            risk_level=AIRecommendation.RISK_LOW,
            reasoning="Product alias proposal.",
            recommendation_json={
                "proposal_type": AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
                "alias_text": "Icon Edp",
                "canonical_text": self.perfume.name,
                "brand_id": self.perfume.brand_id,
                "perfume_id": self.perfume.id,
            },
        )
        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[product_alias_recommendation.pk],
            ),
            {"action": "accept"},
        )

        response = self.client.get(
            reverse("assistant_linking:ai_recommendation_queue"),
            {
                "status": "all",
                "proposal_type": AILearningProposal.PROPOSAL_FRAGRANTICA_LINK_REVIEW,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fragrantica proposal.")
        self.assertNotContains(response, "Product alias proposal.")

    def test_queue_can_filter_recommendations_without_learning_proposals(self):
        with_proposal = self.create_recommendation(reasoning="Accepted proposal row.")
        self.create_recommendation(reasoning="Still pending advice.")
        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[with_proposal.pk],
            ),
            {"action": "accept"},
        )

        response = self.client.get(
            reverse("assistant_linking:ai_recommendation_queue"),
            {"status": "all", "proposal": "none"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Accepted proposal row.")
        self.assertContains(response, "Still pending advice.")

    def test_review_action_accepts_and_resets_recommendation(self):
        recommendation = self.create_recommendation()

        response = self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[recommendation.pk],
            ),
            {"action": "accept"},
        )

        self.assertEqual(response.status_code, 302)
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, AIRecommendation.STATUS_ACCEPTED)
        self.assertIsNotNone(recommendation.reviewed_by_id)
        self.assertIsNotNone(recommendation.reviewed_at)
        proposal = AILearningProposal.objects.get(source_recommendation=recommendation)
        self.assertEqual(
            proposal.proposal_type,
            AILearningProposal.PROPOSAL_FRAGRANTICA_LINK_REVIEW,
        )
        self.assertEqual(proposal.status, AILearningProposal.STATUS_PENDING)
        self.assertEqual(
            proposal.proposed_action_json["fragrantica_product_id"],
            self.source.pk,
        )

        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[recommendation.pk],
            ),
            {"action": "reset"},
        )
        recommendation.refresh_from_db()
        self.assertEqual(recommendation.status, AIRecommendation.STATUS_PENDING)
        self.assertIsNone(recommendation.reviewed_at)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AILearningProposal.STATUS_REJECTED)

    def test_apply_learning_proposal_uses_reviewed_fragrantica_link_action(self):
        recommendation = self.create_recommendation()
        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[recommendation.pk],
            ),
            {"action": "accept"},
        )
        proposal = AILearningProposal.objects.get(source_recommendation=recommendation)

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_apply",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AILearningProposal.STATUS_APPLIED)
        self.assertIsNotNone(proposal.reviewed_by_id)
        self.assertIn("link_result", proposal.impact_json)

        self.source.refresh_from_db()
        self.assertEqual(self.source.match_status, FragranticaProduct.STATUS_LINKED)
        self.assertEqual(self.source.matched_perfume_id, self.perfume.pk)
        self.assertTrue(
            FragranticaProductLink.objects.filter(
                source=self.source,
                perfume=self.perfume,
                link_type=FragranticaProductLink.LINK_TYPE_PRIMARY,
            ).exists()
        )

    def test_bulk_apply_alias_proposals_applies_only_accepted_aliases(self):
        supplier = Supplier.objects.create(name="Bulk Alias Supplier")
        alias_recommendation = AIRecommendation.objects.create(
            task_type=AIRecommendation.TASK_KB_SUGGESTION,
            perfume=self.perfume,
            input_hash="b" * 64,
            prompt_version="manual-pattern-v1",
            model_name="deterministic-pattern-v1",
            confidence=88,
            risk_level=AIRecommendation.RISK_LOW,
            reasoning="Accepted product alias proposal.",
            recommendation_json={
                "proposal_type": AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
                "alias_text": "Icon Edp",
                "canonical_text": self.perfume.name,
                "brand_id": self.perfume.brand_id,
                "perfume_id": self.perfume.id,
                "supplier_id": supplier.id,
            },
        )
        fragrantica_recommendation = self.create_recommendation(
            reasoning="Fragrantica proposal should stay manual."
        )
        for recommendation in [alias_recommendation, fragrantica_recommendation]:
            self.client.post(
                reverse(
                    "assistant_linking:ai_recommendation_review",
                    args=[recommendation.pk],
                ),
                {"action": "accept"},
            )
        alias_proposal = AILearningProposal.objects.get(
            source_recommendation=alias_recommendation
        )
        fragrantica_proposal = AILearningProposal.objects.get(
            source_recommendation=fragrantica_recommendation
        )

        response = self.client.post(
            reverse("assistant_linking:ai_learning_proposal_apply_aliases"),
            {
                "proposal_ids": [str(alias_proposal.pk), str(fragrantica_proposal.pk)],
                "next": reverse("assistant_linking:ai_recommendation_queue"),
            },
        )

        self.assertEqual(response.status_code, 302)
        alias_proposal.refresh_from_db()
        fragrantica_proposal.refresh_from_db()
        self.assertEqual(alias_proposal.status, AILearningProposal.STATUS_APPLIED)
        self.assertEqual(
            fragrantica_proposal.status,
            AILearningProposal.STATUS_PENDING,
        )
        alias = ProductAlias.objects.get(alias_text="Icon Edp")
        self.assertEqual(alias.perfume, self.perfume)
        self.assertEqual(alias.supplier, supplier)

    def test_detail_page_warns_when_alias_proposal_preview_is_stale(self):
        supplier = Supplier.objects.create(name="Stale Alias Supplier")
        products = [
            SupplierProduct.objects.create(
                supplier=supplier,
                name=f"Antonio Banderas Icon EDP stale {index}",
                identity_key=f"stale-alias-{index}",
            )
            for index in range(2)
        ]
        for product in products:
            ParsedSupplierProduct.objects.create(
                supplier_product=product,
                raw_name=product.name,
                normalized_text=product.name.lower(),
                normalized_brand=self.perfume.brand,
                product_name_text="Icon Edp",
                concentration="Eau de Parfum",
                confidence=100,
            )
            ManualLinkDecision.objects.create(
                supplier_product=product,
                perfume=self.perfume,
                decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
            )
        self.client.post(reverse("assistant_linking:ai_recommendation_find_patterns"))
        recommendation = AIRecommendation.objects.get(
            task_type=AIRecommendation.TASK_KB_SUGGESTION
        )
        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[recommendation.pk],
            ),
            {"action": "accept"},
        )
        stale_parse = ParsedSupplierProduct.objects.get(supplier_product=products[0])
        stale_parse.product_name_text = "Different Icon"
        stale_parse.save(update_fields=["product_name_text", "updated_at"])

        response = self.client.get(
            reverse(
                "assistant_linking:ai_recommendation_detail",
                args=[recommendation.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview freshness")
        self.assertContains(response, "Changed")
        self.assertContains(response, "review the impact again before apply")

        proposal = AILearningProposal.objects.get(source_recommendation=recommendation)
        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_apply",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AILearningProposal.STATUS_PENDING)
        self.assertFalse(ProductAlias.objects.filter(alias_text="Icon Edp").exists())

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_regenerate_preview",
                args=[proposal.pk],
            ),
            {
                "next": reverse(
                    "assistant_linking:ai_recommendation_detail",
                    args=[recommendation.pk],
                )
            },
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(
            proposal.impact_json["preview"]["saved_parse_matches"],
            1,
        )
        self.assertIn("preview_refreshed_at", proposal.impact_json)

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_apply",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AILearningProposal.STATUS_APPLIED)
        self.assertTrue(ProductAlias.objects.filter(alias_text="Icon Edp").exists())

    def test_pattern_scan_creates_pending_product_alias_recommendation(self):
        supplier = Supplier.objects.create(name="Pattern Supplier")
        products = [
            SupplierProduct.objects.create(
                supplier=supplier,
                name=f"Antonio Banderas Icon EDP {index}",
                identity_key=f"pattern-{index}",
            )
            for index in range(2)
        ]
        for product in products:
            ParsedSupplierProduct.objects.create(
                supplier_product=product,
                raw_name=product.name,
                normalized_text=product.name.lower(),
                normalized_brand=self.perfume.brand,
                product_name_text="Icon Edp",
                concentration="Eau de Parfum",
                confidence=100,
            )
            ManualLinkDecision.objects.create(
                supplier_product=product,
                perfume=self.perfume,
                decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
            )

        response = self.client.post(
            reverse("assistant_linking:ai_recommendation_find_patterns"),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        recommendation = AIRecommendation.objects.get(
            task_type=AIRecommendation.TASK_KB_SUGGESTION
        )
        self.assertEqual(recommendation.status, AIRecommendation.STATUS_PENDING)
        self.assertEqual(
            recommendation.recommendation_json["proposal_type"],
            AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
        )
        self.assertEqual(recommendation.recommendation_json["alias_text"], "Icon Edp")
        self.assertEqual(
            recommendation.recommendation_json["canonical_text"],
            self.perfume.name,
        )

    def test_accepting_and_applying_product_alias_pattern_creates_alias(self):
        supplier = Supplier.objects.create(name="Alias Supplier")
        products = [
            SupplierProduct.objects.create(
                supplier=supplier,
                name=f"Antonio Banderas Icon EDP alias {index}",
                identity_key=f"alias-{index}",
            )
            for index in range(3)
        ]
        for index, product in enumerate(products):
            ParsedSupplierProduct.objects.create(
                supplier_product=product,
                raw_name=product.name,
                normalized_text=product.name.lower(),
                normalized_brand=self.perfume.brand,
                product_name_text="Icon Edp",
                concentration="Eau de Parfum",
                confidence=100,
                locked_by_human=index == 2,
            )
            ManualLinkDecision.objects.create(
                supplier_product=product,
                perfume=self.perfume,
                decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
            )
        self.client.post(reverse("assistant_linking:ai_recommendation_find_patterns"))
        recommendation = AIRecommendation.objects.get(
            task_type=AIRecommendation.TASK_KB_SUGGESTION
        )

        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[recommendation.pk],
            ),
            {"action": "accept"},
        )
        proposal = AILearningProposal.objects.get(source_recommendation=recommendation)
        self.assertEqual(
            proposal.proposal_type,
            AILearningProposal.PROPOSAL_PRODUCT_ALIAS,
        )
        self.assertEqual(proposal.status, AILearningProposal.STATUS_PENDING)
        self.assertEqual(
            proposal.impact_json["preview"]["saved_parse_matches"],
            3,
        )
        self.assertEqual(
            proposal.impact_json["preview"]["active_supplier_matches"],
            3,
        )
        self.assertEqual(
            proposal.impact_json["preview"]["unlocked_parse_matches"],
            2,
        )

        queue_response = self.client.get(
            reverse("assistant_linking:ai_recommendation_queue"),
            {"status": "all", "proposal": AILearningProposal.STATUS_PENDING},
        )
        self.assertContains(queue_response, "Impact preview:")
        self.assertContains(queue_response, "3 saved parses")

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_apply",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AILearningProposal.STATUS_APPLIED)
        alias = ProductAlias.objects.get(alias_text="Icon Edp")
        self.assertEqual(alias.perfume, self.perfume)
        self.assertEqual(alias.brand, self.perfume.brand)
        self.assertEqual(alias.supplier, supplier)

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_refresh_parses",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.impact_json["last_refresh"]["refreshed"], 2)
        self.assertEqual(proposal.impact_json["last_refresh"]["skipped_locked"], 1)
        self.assertEqual(
            proposal.impact_json["last_refresh"]["matched_before_refresh"],
            3,
        )

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_revert_alias",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        alias.refresh_from_db()
        self.assertEqual(proposal.status, AILearningProposal.STATUS_REVERTED)
        self.assertFalse(alias.active)
        self.assertIn("revert_result", proposal.impact_json)

    def test_accepting_and_applying_brand_alias_pattern_creates_alias(self):
        supplier = Supplier.objects.create(name="Brand Alias Supplier")
        target_brand = Brand.objects.create(name="Maison Francis Kurkdjian")
        target_perfume = Perfume.objects.create(
            brand=target_brand,
            name="Baccarat Rouge 540",
            concentration="Eau de Parfum",
        )
        products = [
            SupplierProduct.objects.create(
                supplier=supplier,
                name=f"MFK Baccarat Rouge 540 EDP {index}",
                identity_key=f"brand-alias-{index}",
            )
            for index in range(3)
        ]
        for index, product in enumerate(products):
            ParsedSupplierProduct.objects.create(
                supplier_product=product,
                raw_name=product.name,
                normalized_text=product.name.lower(),
                detected_brand_text="MFK",
                product_name_text="Baccarat Rouge 540",
                concentration="Eau de Parfum",
                confidence=75,
                locked_by_human=index == 2,
            )
            ManualLinkDecision.objects.create(
                supplier_product=product,
                perfume=target_perfume,
                decision_type=ManualLinkDecision.DECISION_APPROVE_PERFUME,
            )

        response = self.client.post(
            reverse("assistant_linking:ai_recommendation_find_patterns"),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        recommendation = AIRecommendation.objects.get(
            recommendation_json__proposal_type=AILearningProposal.PROPOSAL_BRAND_ALIAS
        )
        self.assertEqual(recommendation.status, AIRecommendation.STATUS_PENDING)
        self.assertEqual(recommendation.recommendation_json["alias_text"], "MFK")
        self.assertEqual(
            recommendation.recommendation_json["canonical_text"],
            target_brand.name,
        )

        self.client.post(
            reverse(
                "assistant_linking:ai_recommendation_review",
                args=[recommendation.pk],
            ),
            {"action": "accept"},
        )
        proposal = AILearningProposal.objects.get(source_recommendation=recommendation)
        self.assertEqual(
            proposal.proposal_type,
            AILearningProposal.PROPOSAL_BRAND_ALIAS,
        )
        self.assertEqual(proposal.status, AILearningProposal.STATUS_PENDING)
        self.assertEqual(
            proposal.impact_json["preview"]["saved_parse_matches"],
            3,
        )
        self.assertEqual(
            proposal.impact_json["preview"]["unlocked_parse_matches"],
            2,
        )

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_apply",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AILearningProposal.STATUS_APPLIED)
        alias = BrandAlias.objects.get(alias_text="MFK")
        self.assertEqual(alias.brand, target_brand)
        self.assertEqual(alias.supplier, supplier)

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_refresh_parses",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.impact_json["last_refresh"]["refreshed"], 2)
        self.assertEqual(proposal.impact_json["last_refresh"]["skipped_locked"], 1)
        refreshed_parse = ParsedSupplierProduct.objects.get(
            supplier_product=products[0]
        )
        locked_parse = ParsedSupplierProduct.objects.get(supplier_product=products[2])
        self.assertEqual(refreshed_parse.normalized_brand, target_brand)
        self.assertIsNone(locked_parse.normalized_brand)

        response = self.client.post(
            reverse(
                "assistant_linking:ai_learning_proposal_revert_alias",
                args=[proposal.pk],
            ),
            {"next": reverse("assistant_linking:ai_recommendation_queue")},
        )

        self.assertEqual(response.status_code, 302)
        proposal.refresh_from_db()
        alias.refresh_from_db()
        self.assertEqual(proposal.status, AILearningProposal.STATUS_REVERTED)
        self.assertFalse(alias.active)
