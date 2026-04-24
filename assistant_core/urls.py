from django.urls import path

from . import views


app_name = "assistant_core"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("knowledge/", views.KnowledgeView.as_view(), name="knowledge"),
    path("rules/", views.RulesView.as_view(), name="rules"),
    path("aliases/", views.AliasesView.as_view(), name="aliases"),
    path("rules/global/new/", views.GlobalRuleCreateView.as_view(), name="global_rule_create"),
    path("rules/supplier/new/", views.SupplierRuleCreateView.as_view(), name="supplier_rule_create"),
    path("knowledge/notes/new/", views.KnowledgeNoteCreateView.as_view(), name="knowledge_note_create"),
    path("rules/<str:model_name>/<int:pk>/disable/", views.RuleDisableView.as_view(), name="rule_disable"),
    path("rules/teach-from-decision/", views.TeachFromDecisionView.as_view(), name="teach_from_decision"),
    path("brand-managers/", views.BrandWatchProfileListView.as_view(), name="brand_manager_list"),
    path("brand-managers/new/", views.BrandWatchProfileCreateView.as_view(), name="brand_manager_create"),
    path("brand-managers/<int:pk>/", views.BrandWatchProfileDetailView.as_view(), name="brand_manager_detail"),
    path("brand-managers/<int:pk>/run-mock/", views.RunMockBrandWatchView.as_view(), name="brand_manager_run_mock"),
    path("research/jobs/", views.ResearchJobListView.as_view(), name="research_jobs"),
    path("research/jobs/<int:pk>/", views.ResearchJobDetailView.as_view(), name="research_job_detail"),
    path("research/changes/<int:pk>/<str:status>/", views.DetectedChangeStatusView.as_view(), name="detected_change_status"),
    path("research/claims/", views.ClaimListView.as_view(), name="claims"),
    path("research/claims/<int:pk>/<str:status>/", views.ClaimStatusView.as_view(), name="claim_status"),
    path("research/drafts/", views.DraftListView.as_view(), name="drafts"),
    path("research/drafts/<int:pk>/<str:status>/", views.DraftStatusView.as_view(), name="draft_status"),
    path("research/perfume/<int:pk>/", views.PerfumeResearchView.as_view(), name="perfume_research"),
    path("research/perfume/<int:perfume_id>/generate-draft/", views.GenerateDraftView.as_view(), name="generate_draft"),
]
