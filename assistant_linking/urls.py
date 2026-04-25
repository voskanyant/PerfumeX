from django.urls import path

from . import views


app_name = "assistant_linking"

urlpatterns = [
    path("normalization/", views.NormalizationDashboardView.as_view(), name="normalization_dashboard"),
    path("normalization/unparsed/", views.UnparsedListView.as_view(), name="normalization_unparsed"),
    path("normalization/parsed/", views.ParsedListView.as_view(), name="normalization_parsed"),
    path("normalization/low-confidence/", views.LowConfidenceListView.as_view(), name="normalization_low_confidence"),
    path("normalization/missing-brand/", views.MissingBrandListView.as_view(), name="normalization_missing_brand"),
    path("normalization/missing-name/", views.MissingNameListView.as_view(), name="normalization_missing_name"),
    path("normalization/missing-concentration/", views.MissingConcentrationListView.as_view(), name="normalization_missing_concentration"),
    path("normalization/missing-size/", views.MissingSizeListView.as_view(), name="normalization_missing_size"),
    path("normalization/tester-sample/", views.TesterSampleListView.as_view(), name="normalization_tester_sample"),
    path("normalization/modifiers/", views.ModifierConflictListView.as_view(), name="normalization_modifiers"),
    path("normalization/garbage/", views.GarbageListView.as_view(), name="normalization_garbage"),
    path("normalization/<int:supplier_product_id>/", views.ParsedProductDetailView.as_view(), name="normalization_detail"),
    path("normalization/<int:supplier_product_id>/reparse/", views.ReparseProductView.as_view(), name="normalization_reparse"),
    path("normalization/<int:supplier_product_id>/exclude-garbage/", views.ExcludeGarbageKeywordView.as_view(), name="normalization_exclude_garbage"),
    path("normalization/<int:supplier_product_id>/lock/", views.LockParseView.as_view(), name="normalization_lock"),
    path("normalization/<int:supplier_product_id>/teach/", views.TeachParseView.as_view(), name="normalization_teach"),
    path("normalization/<int:supplier_product_id>/accept-candidate/", views.AcceptCatalogCandidateView.as_view(), name="normalization_accept_candidate"),
    path("normalization/<int:supplier_product_id>/brand-alias/", views.SaveBrandAliasView.as_view(), name="save_brand_alias"),
    path("normalization/<int:supplier_product_id>/product-alias/", views.SaveProductAliasView.as_view(), name="save_product_alias"),
    path("linking/", views.GroupQueueView.as_view(), name="group_queue"),
    path("linking/rebuild/", views.RebuildGroupsView.as_view(), name="rebuild_groups"),
    path("linking/groups/<int:group_id>/", views.GroupDetailView.as_view(), name="group_detail"),
    path("linking/groups/<int:group_id>/<str:action>/", views.GroupActionView.as_view(), name="group_action"),
    path("linking/product/<int:supplier_product_id>/", views.ProductWorkbenchView.as_view(), name="product_workbench"),
    path("linking/product/<int:supplier_product_id>/suggest/", views.GenerateSuggestionsView.as_view(), name="generate_suggestions"),
    path("linking/product/<int:supplier_product_id>/bulk-link/", views.BulkLinkView.as_view(), name="bulk_link"),
    path("linking/bulk/<int:action_id>/status/", views.BulkLinkStatusView.as_view(), name="bulk_link_status"),
    path("linking/actions/<int:action_id>/undo/", views.UndoLinkActionView.as_view(), name="undo_link_action"),
]
