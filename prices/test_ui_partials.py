from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from django.core.paginator import Paginator
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase


BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_DIRS = [
    BASE_DIR / "prices/templates",
    BASE_DIR / "assistant_core/templates",
    BASE_DIR / "assistant_linking/templates",
]
PRODUCT_LIST_JS = BASE_DIR / "prices/static/prices/js/list-search.js"
SHARED_PAGINATION_COMPONENT = "prices/templates/prices/components/pagination.html"
SHARED_TABS_COMPONENT = "prices/templates/prices/components/tabs.html"
SHARED_COMPONENT_ADAPTER_TEMPLATES = {
    '{% include "prices/components/page_header.html" %}': (
        "prices/templates/includes/page_header.html"
    ),
    '{% include "prices/components/pagination.html" %}': (
        "prices/templates/includes/pagination.html"
    ),
    '{% include "prices/components/tabs.html" %}': (
        "prices/templates/includes/tabs.html"
    ),
    '{% include "prices/components/table_empty.html" %}': (
        "prices/templates/includes/table_empty.html"
    ),
}
ASSISTANT_CORE_PAGINATED_TEMPLATES = [
    "assistant_core/templates/assistant_core/catalog/brands.html",
    "assistant_core/templates/assistant_core/catalog/perfumes.html",
    "assistant_core/templates/assistant_core/catalog/variants.html",
    "assistant_core/templates/assistant_core/knowledge/aliases.html",
    "assistant_core/templates/assistant_core/knowledge/index.html",
]
ASSISTANT_CORE_HEADER_TEMPLATES = ASSISTANT_CORE_PAGINATED_TEMPLATES + [
    "assistant_core/templates/assistant_core/dashboard.html",
    "assistant_core/templates/assistant_core/brand_managers/detail.html",
    "assistant_core/templates/assistant_core/brand_managers/list.html",
    "assistant_core/templates/assistant_core/catalog/cleanup.html",
    "assistant_core/templates/assistant_core/catalog/confirm_delete.html",
    "assistant_core/templates/assistant_core/catalog/form.html",
    "assistant_core/templates/assistant_core/catalog/import.html",
    "assistant_core/templates/assistant_core/form.html",
    "assistant_core/templates/assistant_core/knowledge/alias_confirm_delete.html",
    "assistant_core/templates/assistant_core/knowledge/alias_form.html",
    "assistant_core/templates/assistant_core/research/claims.html",
    "assistant_core/templates/assistant_core/research/drafts.html",
    "assistant_core/templates/assistant_core/research/jobs.html",
    "assistant_core/templates/assistant_core/research/job_detail.html",
    "assistant_core/templates/assistant_core/research/perfume.html",
]
ASSISTANT_CORE_TAB_WRAPPER_TEMPLATES = [
    "assistant_core/templates/assistant_core/catalog/_nav.html",
]
ASSISTANT_CORE_TABLE_EMPTY_TEMPLATES = [
    "assistant_core/templates/assistant_core/knowledge/aliases.html",
    "assistant_core/templates/assistant_core/knowledge/index.html",
]
ASSISTANT_CORE_EMPTY_STATE_TEMPLATES = [
    "assistant_core/templates/assistant_core/dashboard.html",
]
PRICES_TAB_WRAPPER_TEMPLATES = [
    "prices/templates/prices/_import_tabs.html",
    "prices/templates/prices/our_products_catalog.html",
    "prices/templates/prices/supplier_import.html",
]
PRICES_PAGINATED_TEMPLATES = [
    "prices/templates/prices/fragrantica_products.html",
    "prices/templates/prices/list.html",
    "prices/templates/prices/our_products_catalog.html",
]
PRICES_NAMED_PAGINATED_TEMPLATES = [
    "prices/templates/prices/product_detail.html",
    "prices/templates/prices/import_detailed_logs.html",
]
PRICES_TABLE_EMPTY_TEMPLATES = [
    "prices/templates/prices/currencies.html",
    "prices/templates/prices/import_detail.html",
    "prices/templates/prices/import_settings.html",
    "prices/templates/prices/list.html",
    "prices/templates/prices/our_product_detail.html",
    "prices/templates/prices/our_products_catalog.html",
    "prices/templates/prices/stuck_email_import_runs.html",
    "prices/templates/prices/supplier_overview.html",
]
PRICES_EMPTY_STATE_TEMPLATES = [
    "prices/templates/prices/currencies.html",
    "prices/templates/prices/fragrantica_products.html",
    "prices/templates/prices/import_detail.html",
    "prices/templates/prices/import_detailed_logs.html",
    "prices/templates/prices/our_product_detail.html",
    "prices/templates/prices/our_products_catalog.html",
    "prices/templates/prices/stuck_email_import_runs.html",
    "prices/templates/prices/supplier_overview.html",
]
ASSISTANT_LINKING_SIMPLE_HEADER_TEMPLATES = [
    "assistant_linking/templates/assistant_linking/normalization/product_list.html",
    "assistant_linking/templates/assistant_linking/normalization/low_confidence.html",
    "assistant_linking/templates/assistant_linking/normalization/issue_list.html",
]
ASSISTANT_LINKING_SIMPLE_PAGINATED_TEMPLATES = [
    "assistant_linking/templates/assistant_linking/normalization/product_list.html",
    "assistant_linking/templates/assistant_linking/normalization/low_confidence.html",
    "assistant_linking/templates/assistant_linking/normalization/issue_list.html",
]
ASSISTANT_LINKING_TABLE_EMPTY_TEMPLATES = [
    "assistant_linking/templates/assistant_linking/groups/detail.html",
    "assistant_linking/templates/assistant_linking/groups/queue.html",
    "assistant_linking/templates/assistant_linking/normalization/detail.html",
    "assistant_linking/templates/assistant_linking/normalization/product_list.html",
    "assistant_linking/templates/assistant_linking/normalization/low_confidence.html",
    "assistant_linking/templates/assistant_linking/normalization/issue_list.html",
    "assistant_linking/templates/assistant_linking/workbench/product.html",
]
ASSISTANT_LINKING_EMPTY_STATE_TEMPLATES = [
    "assistant_linking/templates/assistant_linking/workbench/product.html",
]
ASSISTANT_LINKING_COMPLEX_HEADER_TEMPLATES = [
    "assistant_linking/templates/assistant_linking/groups/queue.html",
    "assistant_linking/templates/assistant_linking/groups/detail.html",
]
ASSISTANT_LINKING_DASHBOARD_HEADER_TEMPLATES = [
    "assistant_linking/templates/assistant_linking/normalization/dashboard.html",
]
ASSISTANT_LINKING_META_HEADER_TEMPLATES = [
    "assistant_linking/templates/assistant_linking/normalization/detail.html",
    "assistant_linking/templates/assistant_linking/workbench/product.html",
]
ASSISTANT_LINKING_HEADER_ACTION_PARTIALS = [
    "assistant_linking/templates/assistant_linking/groups/_queue_header_actions.html",
    "assistant_linking/templates/assistant_linking/groups/_detail_header_actions.html",
]
ASSISTANT_LINKING_TOP_LEVEL_HEADER_TEMPLATES = (
    ASSISTANT_LINKING_SIMPLE_HEADER_TEMPLATES
    + ASSISTANT_LINKING_COMPLEX_HEADER_TEMPLATES
    + ASSISTANT_LINKING_DASHBOARD_HEADER_TEMPLATES
    + ASSISTANT_LINKING_META_HEADER_TEMPLATES
)
ASSISTANT_LINKING_ALLOWED_SECTION_PAGE_HEADS = {
    "assistant_linking/templates/assistant_linking/normalization/dashboard.html": 1,
    "assistant_linking/templates/assistant_linking/normalization/detail.html": 1,
}
ASSISTANT_CORE_SHARED_HEADER_TEMPLATES_WITH_NO_MANUAL_PAGE_HEAD = [
    "assistant_core/templates/assistant_core/brand_managers/detail.html",
    "assistant_core/templates/assistant_core/brand_managers/list.html",
    "assistant_core/templates/assistant_core/catalog/confirm_delete.html",
    "assistant_core/templates/assistant_core/form.html",
    "assistant_core/templates/assistant_core/knowledge/alias_confirm_delete.html",
    "assistant_core/templates/assistant_core/knowledge/alias_form.html",
    "assistant_core/templates/assistant_core/research/claims.html",
    "assistant_core/templates/assistant_core/research/drafts.html",
    "assistant_core/templates/assistant_core/research/jobs.html",
    "assistant_core/templates/assistant_core/research/job_detail.html",
    "assistant_core/templates/assistant_core/research/perfume.html",
]
PRICES_SHARED_HEADER_TEMPLATES_WITH_NO_MANUAL_PAGE_HEADER = [
    "prices/templates/prices/confirm_delete.html",
    "prices/templates/prices/currencies.html",
    "prices/templates/prices/dashboard.html",
    "prices/templates/prices/documentation.html",
    "prices/templates/prices/form.html",
    "prices/templates/prices/fragrantica_products.html",
    "prices/templates/prices/import_detail.html",
    "prices/templates/prices/import_detailed_logs.html",
    "prices/templates/prices/import_settings.html",
    "prices/templates/prices/import_wizard.html",
    "prices/templates/prices/our_product_detail.html",
    "prices/templates/prices/our_products_catalog.html",
    "prices/templates/prices/product_detail.html",
    "prices/templates/prices/product_linking.html",
    "prices/templates/prices/stuck_email_import_runs.html",
    "prices/templates/prices/supplier_detail.html",
]
PRICES_DETAIL_HEADER_ACTION_PARTIALS = [
    "prices/templates/prices/_import_detail_header_actions.html",
    "prices/templates/prices/_our_product_detail_header_actions.html",
    "prices/templates/prices/_supplier_detail_header_actions.html",
]
PRICES_SHARED_HEADER_TEMPLATES_WITH_ALLOWED_SECTION_HEADERS = {
    "prices/templates/prices/supplier_overview.html": 1,
}
SPECIALIZED_HEADER_EXCEPTION_MARKERS = {
    "prices/templates/prices/list.html": [
        "products-page-header",
        "generic-list-header",
    ],
    "prices/templates/prices/supplier_import.html": [
        "supplier-import-hero",
    ],
    "prices/templates/prices/supplier_overview.html": [
        '<div class="page-header space-bottom-none">',
    ],
    "assistant_linking/templates/assistant_linking/normalization/dashboard.html": [
        '<div class="page-head">',
    ],
    "assistant_linking/templates/assistant_linking/normalization/detail.html": [
        '<div class="page-head">',
    ],
}
MANUAL_HEADER_WRAPPER_MARKERS = [
    '<div class="page-header',
    '<div class="page-head">',
    "products-page-header",
    "generic-list-header",
    "supplier-import-hero",
]


class FakeMessage:
    tags = "success"

    def __str__(self):
        return "Saved successfully"


class SharedUiPartialRenderTests(SimpleTestCase):
    def iter_templates(self):
        for template_dir in TEMPLATE_DIRS:
            yield from template_dir.rglob("*.html")

    def render_include(self, include_path: str, context: dict | None = None) -> str:
        template = Template('{% include "' + include_path + '" %}')
        return template.render(Context(context or {}))

    def test_page_header_include_renders_standard_structure_and_actions(self):
        html = self.render_include(
            "includes/page_header.html",
            {
                "kicker": "Imports",
                "title": "Supplier board",
                "title_prefix": "Staff ",
                "subtitle": "Review supplier import health.",
                "subtitle_class": "space-bottom-none",
                "meta": "Latest mailbox and price-file activity",
                "primary_url": "/admin/imports/new/",
                "primary_label": "Import prices",
                "secondary_url": "/admin/imports/",
                "secondary_label": "View imports",
            },
        )

        self.assertIn('class="page-header"', html)
        self.assertIn('class="page-kicker">Imports</span>', html)
        self.assertIn('class="page-title">Staff Supplier board</h1>', html)
        self.assertIn(
            'class="page-subtitle space-bottom-none">Review supplier import health.</p>',
            html,
        )
        self.assertIn('class="page-actions"', html)
        self.assertIn('class="button primary"', html)
        self.assertIn('class="button ghost"', html)

    def test_page_header_include_renders_optional_meta_template(self):
        html = self.render_include(
            "includes/page_header.html",
            {
                "kicker": "Assistant",
                "title": "Normalisation",
                "meta_template": (
                    "assistant_linking/normalization/_dashboard_header_meta.html"
                ),
                "hidden_keywords_active": True,
                "stats_available": False,
            },
        )

        self.assertIn("Global hidden product keywords are active", html)
        self.assertIn("Stats snapshot is not generated yet", html)

    def test_tabs_include_renders_standard_tabs_container_and_items(self):
        html = self.render_include(
            "includes/tabs.html",
            {
                "label": "Import sections",
                "class_name": "import-tabs",
                "items_template": "prices/_import_tab_items.html",
                "import_section": "overview",
                "overview_url": "/admin/imports/",
                "user": SimpleNamespace(is_staff=False),
            },
        )

        self.assertIn('class="tabs import-tabs"', html)
        self.assertIn('aria-label="Import sections"', html)
        self.assertIn('class="tab active"', html)
        self.assertIn('href="/admin/imports/"', html)

    def test_pagination_include_preserves_filters_and_renders_jump_form(self):
        request = RequestFactory().get(
            "/admin/products/",
            {"tab": "products", "q": "oud", "supplier": "7", "page": "3"},
        )
        page_obj = Paginator(list(range(80)), 10).page(3)

        html = self.render_include(
            "includes/pagination.html",
            {
                "request": request,
                "page_obj": page_obj,
                "page_param": "page",
                "label": "Product pages",
                "jump_id": "product-page-jump",
            },
        )

        self.assertIn('class="pagination-shell"', html)
        self.assertIn('aria-label="Product pages"', html)
        self.assertIn("tab=products", html)
        self.assertIn("q=oud", html)
        self.assertIn("supplier=7", html)
        self.assertIn('class="pagination-jump"', html)
        self.assertIn('id="product-page-jump"', html)
        self.assertIn("Page 3 of 8", html)

    def test_pagination_include_supports_optional_html_id(self):
        request = RequestFactory().get("/products/", {"page": "2"})
        page_obj = Paginator(list(range(30)), 10).page(2)

        html = self.render_include(
            "includes/pagination.html",
            {
                "request": request,
                "page_obj": page_obj,
                "html_id": "pagination-controls",
                "class_name": "workspace-pagination",
            },
        )

        self.assertIn('id="pagination-controls"', html)
        self.assertIn('class="pagination-shell workspace-pagination"', html)

    def test_pagination_include_renders_standard_gap_marker(self):
        request = RequestFactory().get("/products/", {"page": "2"})
        page_obj = Paginator(list(range(100)), 10).page(2)

        html = self.render_include(
            "includes/pagination.html",
            {
                "request": request,
                "page_obj": page_obj,
            },
        )

        self.assertIn('class="page-link is-disabled"', html)
        self.assertIn('aria-hidden="true">...</span>', html)

    def test_pagination_include_uses_elided_page_range_for_large_queues(self):
        request = RequestFactory().get("/products/", {"page": "500"})
        page_obj = Paginator(list(range(10000)), 10).page(500)

        html = self.render_include(
            "includes/pagination.html",
            {
                "request": request,
                "page_obj": page_obj,
            },
        )

        self.assertIn('class="page-link is-active">500</span>', html)
        self.assertIn("Page 500 of 1000", html)
        self.assertIn('aria-hidden="true">...</span>', html)
        self.assertNotIn(">250</a>", html)
        self.assertNotIn(">750</a>", html)

    def test_pagination_include_can_drop_action_only_query_params(self):
        request = RequestFactory().get(
            "/admin/assistant/normalization/issues/",
            {"q": "oud", "page": "2", "refresh": "1"},
        )
        page_obj = Paginator(list(range(80)), 10).page(2)

        html = self.render_include(
            "includes/pagination.html",
            {
                "request": request,
                "page_obj": page_obj,
                "page_param": "page",
                "exclude_query_params": "refresh",
                "jump_id": "issue-page-jump",
            },
        )

        self.assertIn("q=oud", html)
        self.assertIn("page=1", html)
        self.assertNotIn("refresh=1", html)
        self.assertNotIn('name="refresh"', html)

    def test_pagination_include_supports_named_page_param_and_boundary_links(self):
        request = RequestFactory().get(
            "/products/7/",
            {
                "start": "2026-04-01",
                "chart_currency": "rub",
                "history_page": "2",
            },
        )
        page_obj = Paginator(list(range(80)), 10).page(2)

        html = self.render_include(
            "includes/pagination.html",
            {
                "request": request,
                "page_obj": page_obj,
                "page_param": "history_page",
                "include_boundary_links": True,
                "jump_id": "history-page-jump",
            },
        )

        self.assertIn(">First</a>", html)
        self.assertIn(">Last</a>", html)
        self.assertIn("history_page=1", html)
        self.assertIn("history_page=8", html)
        self.assertIn('name="start" value="2026-04-01"', html)
        self.assertIn('name="chart_currency" value="rub"', html)
        self.assertNotIn('type="hidden" name="history_page"', html)

    def test_empty_state_include_renders_default_and_optional_cta(self):
        html = self.render_include(
            "includes/empty_state.html",
            {
                "title": "No imports",
                "message": "Upload a supplier file to start.",
                "cta_url": "/admin/import-prices/",
                "cta_label": "Upload file",
            },
        )

        self.assertIn('class="empty-state"', html)
        self.assertIn('class="empty-state-title">No imports</div>', html)
        self.assertIn("Upload a supplier file to start.", html)
        self.assertIn('class="button ghost"', html)

    def test_table_empty_include_renders_table_row_and_optional_cta(self):
        html = self.render_include(
            "includes/table_empty.html",
            {
                "colspan": 3,
                "message": "No products found.",
                "cta_url": "/admin/imports/",
                "cta_label": "Run an import",
            },
        )

        self.assertIn("<tr>", html)
        self.assertIn('colspan="3"', html)
        self.assertIn('class="table-empty-cell"', html)
        self.assertIn("No products found.", html)
        self.assertIn('href="/admin/imports/"', html)

        custom_html = self.render_include(
            "includes/table_empty.html",
            {
                "colspan": 4,
                "class_name": "products-empty-state",
                "message": "No records yet.",
            },
        )

        self.assertIn('colspan="4"', custom_html)
        self.assertIn('class="products-empty-state"', custom_html)
        self.assertIn("No records yet.", custom_html)

    def test_messages_include_renders_flash_stack(self):
        html = self.render_include(
            "includes/messages.html",
            {"messages": [FakeMessage()]},
        )

        self.assertIn('class="flash-stack"', html)
        self.assertIn('class="flash flash--success"', html)
        self.assertIn("Saved successfully", html)
        self.assertIn('aria-label="Dismiss"', html)

    def test_shared_components_are_only_called_by_include_adapters(self):
        for template_path in self.iter_templates():
            relative_path = template_path.relative_to(BASE_DIR).as_posix()
            content = template_path.read_text(encoding="utf-8")

            with self.subTest(template=relative_path):
                for (
                    include_tag,
                    adapter_template,
                ) in SHARED_COMPONENT_ADAPTER_TEMPLATES.items():
                    component_path = include_tag.removeprefix('{% include "')
                    component_path = component_path.removesuffix('" %}')
                    if relative_path == adapter_template:
                        self.assertIn(include_tag, content)
                    else:
                        self.assertNotIn(
                            f'{{% include "{component_path}',
                            content,
                        )

    def test_templates_do_not_hand_roll_pagination_internals(self):
        manual_pagination_markers = [
            ".previous_page_number",
            ".next_page_number",
            ".paginator.page_range",
            ".has_previous",
            ".has_next",
        ]

        for template_path in self.iter_templates():
            relative_path = template_path.relative_to(BASE_DIR).as_posix()
            if relative_path == SHARED_PAGINATION_COMPONENT:
                continue
            content = template_path.read_text(encoding="utf-8")

            with self.subTest(template=relative_path):
                for marker in manual_pagination_markers:
                    self.assertNotIn(marker, content)

    def test_templates_do_not_hand_roll_tab_wrappers(self):
        manual_tab_wrappers = [
            '<nav class="tabs',
            '<div class="tabs',
        ]

        for template_path in self.iter_templates():
            relative_path = template_path.relative_to(BASE_DIR).as_posix()
            if relative_path == SHARED_TABS_COMPONENT:
                continue
            content = template_path.read_text(encoding="utf-8")

            with self.subTest(template=relative_path):
                for marker in manual_tab_wrappers:
                    self.assertNotIn(marker, content)

    def test_manual_header_wrappers_are_explicit_specialized_exceptions(self):
        for template_path in self.iter_templates():
            relative_path = template_path.relative_to(BASE_DIR).as_posix()
            if relative_path == "prices/templates/prices/components/page_header.html":
                continue

            content = template_path.read_text(encoding="utf-8")
            allowed_markers = SPECIALIZED_HEADER_EXCEPTION_MARKERS.get(
                relative_path,
                [],
            )

            with self.subTest(template=relative_path):
                for marker in MANUAL_HEADER_WRAPPER_MARKERS:
                    marker_is_allowed = any(
                        marker in allowed_marker for allowed_marker in allowed_markers
                    )
                    if marker_is_allowed:
                        self.assertIn(marker, content)
                    else:
                        self.assertNotIn(marker, content)

    def test_assistant_core_paginators_use_shared_include_entrypoint(self):
        for template_path in ASSISTANT_CORE_PAGINATED_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/pagination.html"', content)
                self.assertNotIn(
                    '{% include "prices/components/pagination.html"',
                    content,
                )

    def test_assistant_core_page_headers_use_shared_include_entrypoint(self):
        for template_path in ASSISTANT_CORE_HEADER_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertNotIn(
                    '{% include "prices/components/page_header.html"',
                    content,
                )

    def test_assistant_core_tabs_use_shared_include_entrypoint(self):
        for template_path in ASSISTANT_CORE_TAB_WRAPPER_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/tabs.html"', content)
                self.assertNotIn(
                    '{% include "prices/components/tabs.html"',
                    content,
                )

    def test_prices_tabs_use_shared_include_entrypoint(self):
        for template_path in PRICES_TAB_WRAPPER_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/tabs.html"', content)
                self.assertNotIn('<nav class="tabs', content)
                self.assertNotIn(
                    '{% include "prices/components/tabs.html"',
                    content,
                )

    def test_prices_paginators_use_shared_include_entrypoint(self):
        for template_path in PRICES_PAGINATED_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/pagination.html"', content)
                self.assertNotIn('<div class="pagination">', content)
                self.assertNotIn("page_obj.previous_page_number", content)
                self.assertNotIn("page_obj.next_page_number", content)

    def test_product_list_pagination_preserves_live_search_hook(self):
        content = (BASE_DIR / "prices/templates/prices/list.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('html_id="pagination-controls"', content)
        self.assertIn('class_name="workspace-pagination"', content)

    def test_product_list_ajax_pagination_uses_shared_markup_classes(self):
        content = PRODUCT_LIST_JS.read_text(encoding="utf-8")

        self.assertIn('el("div", "pagination-list")', content)
        self.assertIn('"pagination-jump"', content)
        self.assertIn('"pagination-summary"', content)
        self.assertIn('"page-link is-active"', content)
        self.assertIn('"page-link is-disabled"', content)
        self.assertNotIn('el("nav", "space-top-md")', content)
        self.assertNotIn('"page-item active"', content)

    def test_prices_named_paginators_use_shared_include_entrypoint(self):
        for template_path in PRICES_NAMED_PAGINATED_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/pagination.html"', content)
                self.assertNotIn("history_page_obj.previous_page_number", content)
                self.assertNotIn("history_page_obj.next_page_number", content)
                self.assertNotIn("history_querystring", content)
                self.assertNotIn("runs_page.previous_page_number", content)
                self.assertNotIn("diagnostics_page.previous_page_number", content)
                self.assertNotIn("batches_page.previous_page_number", content)

    def test_import_detailed_logs_paginators_keep_independent_page_params(self):
        content = (
            BASE_DIR / "prices/templates/prices/import_detailed_logs.html"
        ).read_text(encoding="utf-8")

        self.assertEqual(content.count('{% include "includes/pagination.html"'), 3)
        self.assertIn('page_param="page"', content)
        self.assertIn('exclude_query_params="dpage,bpage"', content)
        self.assertIn('page_param="dpage"', content)
        self.assertIn('exclude_query_params="page,bpage"', content)
        self.assertIn('page_param="bpage"', content)
        self.assertIn('exclude_query_params="page,dpage"', content)

    def test_prices_our_products_catalog_tab_items_preserve_existing_links(self):
        content = (
            BASE_DIR / "prices/templates/prices/_our_products_catalog_tab_items.html"
        ).read_text(encoding="utf-8")

        self.assertIn("prices:our_product_list", content)
        self.assertIn("prices:catalogue_linking_workbench", content)
        self.assertIn("tab=brands", content)
        self.assertIn("tab=collections", content)
        self.assertIn("tab=concentrations", content)
        self.assertIn("prices:our_product_concentration_audit", content)
        self.assertEqual(content.count('class="tab'), 6)

    def test_prices_supplier_import_tab_items_preserve_existing_links(self):
        content = (
            BASE_DIR / "prices/templates/prices/_supplier_import_source_tab_items.html"
        ).read_text(encoding="utf-8")

        self.assertIn("source=email", content)
        self.assertIn("source=link", content)
        self.assertIn("source=file", content)
        self.assertIn("active_import_source", content)
        self.assertEqual(content.count('class="tab'), 3)

    def test_prices_table_empty_rows_use_shared_include_entrypoint(self):
        manual_empty_rows = [
            '<td colspan="6" class="muted">No mailboxes yet.</td>',
            '<td colspan="5" class="empty-state">',
            '<td colspan="6" class="empty-state">',
            '<td colspan="7" class="empty-state">',
            '<tr><td colspan="3" class="empty-state">No brands yet.</td></tr>',
            '<tr><td colspan="4" class="empty-state">No collections yet.</td></tr>',
            '<tr><td colspan="4" class="empty-state">No concentrations yet.</td></tr>',
            'class="products-empty-state">No records yet.</td>',
        ]

        for template_path in PRICES_TABLE_EMPTY_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/table_empty.html"', content)
                for manual_empty_row in manual_empty_rows:
                    self.assertNotIn(manual_empty_row, content)

    def test_prices_empty_states_use_shared_include_entrypoint(self):
        manual_empty_states = [
            '<div class="empty-state">No Fragrantica catalogue rows match these filters.</div>',
            '<div class="empty-state space-top-sm">',
            '<div class="empty-state space-top-md">',
            '<div class="products-empty-state">',
            'No run logs yet. <a href="{% url',
            'No attachment diagnostics yet. <a href="{% url',
            'No import batches yet. <a href="{% url',
            'No suppliers yet.{% if user.is_staff %} <a href="{% url',
            'No catalogue products yet. <a href="{% url',
        ]

        for template_path in PRICES_EMPTY_STATE_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/empty_state.html"', content)
                for manual_empty_state in manual_empty_states:
                    self.assertNotIn(manual_empty_state, content)

    def test_assistant_core_table_empty_rows_use_shared_include_entrypoint(self):
        manual_empty_rows = [
            'No entries match the current filters. <a href="{% url',
            'class="empty-state"',
        ]

        for template_path in ASSISTANT_CORE_TABLE_EMPTY_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/table_empty.html"', content)
                for manual_empty_row in manual_empty_rows:
                    self.assertNotIn(manual_empty_row, content)

    def test_assistant_core_empty_states_use_shared_include_entrypoint(self):
        manual_empty_states = [
            '<div class="empty-state">',
            'No assistant queues are configured. <a href="{% url',
        ]

        for template_path in ASSISTANT_CORE_EMPTY_STATE_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/empty_state.html"', content)
                for manual_empty_state in manual_empty_states:
                    self.assertNotIn(manual_empty_state, content)

    def test_assistant_linking_table_empty_rows_use_shared_include_entrypoint(self):
        manual_empty_rows = [
            'No unparsed rows. <a href="{% url',
            'No low-confidence rows. <a href="{% url',
            'No rows in this queue. <a href="{% url',
            '<td colspan="6" class="empty-state">No groups yet.',
            'No catalogue candidates yet. <a href="{% url',
            "No preview rows matched this teaching rule.</td>",
            'No similar supplier rows found. <a href="{% url',
            'No rows in this group. <a href="{% url',
            'No similar products. <a href="{% url',
            'class="empty-state"',
        ]

        for template_path in ASSISTANT_LINKING_TABLE_EMPTY_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/table_empty.html"', content)
                for manual_empty_row in manual_empty_rows:
                    self.assertNotIn(manual_empty_row, content)

    def test_assistant_linking_empty_states_use_shared_include_entrypoint(self):
        manual_empty_states = [
            '<div class="empty-state">',
            '<div class="empty-state">No suggestions yet.',
            'No related rows. <a href="{% url',
        ]

        for template_path in ASSISTANT_LINKING_EMPTY_STATE_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/empty_state.html"', content)
                for manual_empty_state in manual_empty_states:
                    self.assertNotIn(manual_empty_state, content)

    def test_assistant_linking_simple_pagination_uses_shared_include_entrypoint(
        self,
    ):
        for template_path in ASSISTANT_LINKING_SIMPLE_PAGINATED_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/pagination.html"', content)
                self.assertNotIn("workspace-pagination", content)
                self.assertNotIn("workspace-page-link", content)
                self.assertNotIn("page_obj.previous_page_number", content)
                self.assertNotIn("page_obj.next_page_number", content)

    def test_assistant_core_migrated_top_level_headers_do_not_use_page_head(self):
        for (
            template_path
        ) in ASSISTANT_CORE_SHARED_HEADER_TEMPLATES_WITH_NO_MANUAL_PAGE_HEAD:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertNotIn('<div class="page-head">', content)

    def test_assistant_linking_simple_headers_use_shared_include_entrypoint(self):
        for template_path in ASSISTANT_LINKING_SIMPLE_HEADER_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertNotIn('<div class="page-head">', content)

    def test_assistant_linking_complex_headers_use_shared_include_entrypoint(self):
        expected_action_templates = {
            "assistant_linking/templates/assistant_linking/groups/queue.html": (
                'actions_template="assistant_linking/groups/_queue_header_actions.html"'
            ),
            "assistant_linking/templates/assistant_linking/groups/detail.html": (
                'actions_template="assistant_linking/groups/_detail_header_actions.html"'
            ),
        }
        for template_path in ASSISTANT_LINKING_COMPLEX_HEADER_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertIn(expected_action_templates[template_path], content)
                self.assertNotIn('<div class="page-head">', content)

    def test_assistant_linking_dashboard_header_uses_shared_include_entrypoint(self):
        for template_path in ASSISTANT_LINKING_DASHBOARD_HEADER_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")
                first_workspace = content.split(
                    '<div class="workspace-block space-top-md">',
                    maxsplit=1,
                )[0]

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertIn(
                    'meta_template="assistant_linking/normalization/_dashboard_header_meta.html"',
                    content,
                )
                self.assertIn(
                    'actions_template="assistant_linking/normalization/_dashboard_header_actions.html"',
                    content,
                )
                self.assertNotIn('<div class="page-head">', first_workspace)

    def test_assistant_linking_meta_headers_use_shared_include_entrypoint(self):
        expected_templates = {
            "assistant_linking/templates/assistant_linking/normalization/detail.html": {
                "meta": 'meta_template="assistant_linking/normalization/_detail_header_meta.html"',
                "actions": 'actions_template="assistant_linking/normalization/_detail_header_actions.html"',
                "first_section_end": "{% url 'assistant_linking:normalization_dashboard'",
            },
            "assistant_linking/templates/assistant_linking/workbench/product.html": {
                "meta": 'meta_template="assistant_linking/workbench/_product_header_meta.html"',
                "actions": 'actions_template="assistant_linking/workbench/_product_header_actions.html"',
                "first_section_end": '<div class="page-grid-3">',
            },
        }
        for template_path in ASSISTANT_LINKING_META_HEADER_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")
                first_section = content.split(
                    expected_templates[template_path]["first_section_end"],
                    maxsplit=1,
                )[0]

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertIn(expected_templates[template_path]["meta"], content)
                self.assertIn(expected_templates[template_path]["actions"], content)
                self.assertNotIn('<div class="page-head">', first_section)

    def test_assistant_linking_header_actions_preserve_existing_actions(self):
        for template_path in ASSISTANT_LINKING_HEADER_ACTION_PARTIALS:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn("assistant_linking:undo_link_action", content)
                self.assertIn("data-shortcut-help", content)
        queue_actions = (
            BASE_DIR
            / "assistant_linking/templates/assistant_linking/groups/_queue_header_actions.html"
        ).read_text(encoding="utf-8")
        self.assertIn("assistant_linking:rebuild_groups", queue_actions)

    def test_assistant_linking_dashboard_header_partials_preserve_existing_markup(self):
        meta = (
            BASE_DIR
            / "assistant_linking/templates/assistant_linking/normalization/_dashboard_header_meta.html"
        ).read_text(encoding="utf-8")
        actions = (
            BASE_DIR
            / "assistant_linking/templates/assistant_linking/normalization/_dashboard_header_actions.html"
        ).read_text(encoding="utf-8")

        self.assertIn("hidden_keywords_active", meta)
        self.assertIn("stats_available", meta)
        self.assertIn("stats_stale", meta)
        self.assertIn("stats_generated_at", meta)
        self.assertIn("assistant_linking:normalization_dashboard", actions)
        self.assertIn("assistant_linking:rebuild_groups", actions)

    def test_assistant_linking_normalization_detail_header_partials_preserve_existing_markup(
        self,
    ):
        meta = (
            BASE_DIR
            / "assistant_linking/templates/assistant_linking/normalization/_detail_header_meta.html"
        ).read_text(encoding="utf-8")
        actions = (
            BASE_DIR
            / "assistant_linking/templates/assistant_linking/normalization/_detail_header_actions.html"
        ).read_text(encoding="utf-8")

        self.assertIn("product.supplier", meta)
        self.assertIn("product.supplier_sku", meta)
        self.assertIn("assistant_linking:normalization_reparse", actions)
        self.assertIn('name="force"', actions)
        self.assertIn("assistant_linking:normalization_lock", actions)
        self.assertIn("assistant_linking:product_workbench", actions)

    def test_assistant_linking_workbench_header_partials_preserve_existing_markup(self):
        meta = (
            BASE_DIR
            / "assistant_linking/templates/assistant_linking/workbench/_product_header_meta.html"
        ).read_text(encoding="utf-8")
        actions = (
            BASE_DIR
            / "assistant_linking/templates/assistant_linking/workbench/_product_header_actions.html"
        ).read_text(encoding="utf-8")

        self.assertIn("product.supplier", meta)
        self.assertIn("product.supplier_sku", meta)
        self.assertIn("assistant_linking:undo_link_action", actions)
        self.assertIn("data-undo-link-action", actions)
        self.assertIn("assistant_linking:generate_suggestions", actions)

    def test_assistant_linking_top_level_pages_keep_shared_header_boundary(self):
        for template_path in ASSISTANT_LINKING_TOP_LEVEL_HEADER_TEMPLATES:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertEqual(
                    content.count('<div class="page-head">'),
                    ASSISTANT_LINKING_ALLOWED_SECTION_PAGE_HEADS.get(
                        template_path,
                        0,
                    ),
                )

    def test_prices_migrated_top_level_headers_use_shared_include_entrypoint(self):
        for template_path in PRICES_SHARED_HEADER_TEMPLATES_WITH_NO_MANUAL_PAGE_HEADER:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertNotIn('<div class="page-header">', content)
                self.assertNotIn('<div class="page-head">', content)

    def test_prices_migrated_top_level_headers_keep_section_header_boundary(self):
        for (
            template_path,
            allowed_page_headers,
        ) in PRICES_SHARED_HEADER_TEMPLATES_WITH_ALLOWED_SECTION_HEADERS.items():
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")
                top_section = content.split(
                    '<div class="email-update-banner',
                    maxsplit=1,
                )[0]

                self.assertIn('{% include "includes/page_header.html"', content)
                self.assertNotIn('<div class="page-header', top_section)
                self.assertEqual(
                    content.count('<div class="page-header'),
                    allowed_page_headers,
                )
                self.assertNotIn('<div class="page-head">', content)

    def test_prices_dashboard_header_actions_preserve_existing_actions(self):
        content = (
            BASE_DIR / "prices/templates/prices/_dashboard_header_actions.html"
        ).read_text(encoding="utf-8")

        self.assertIn("prices:supplier_overview", content)
        self.assertIn("prices:product_list", content)
        self.assertIn('class="button secondary"', content)
        self.assertIn('class="button"', content)

    def test_prices_fragrantica_header_partials_preserve_existing_markup(self):
        actions = (
            BASE_DIR
            / "prices/templates/prices/_fragrantica_products_header_actions.html"
        ).read_text(encoding="utf-8")
        meta = (
            BASE_DIR / "prices/templates/prices/_fragrantica_products_header_meta.html"
        ).read_text(encoding="utf-8")

        self.assertIn("prices:our_product_list", actions)
        self.assertIn("prices:catalogue_linking_workbench", actions)
        self.assertIn("assistant_core:catalog_import", actions)
        self.assertIn('class="button secondary"', actions)
        self.assertIn('class="button primary"', actions)
        self.assertIn("filtered_count_display", meta)
        self.assertIn("current Fragrantica filters", meta)
        self.assertNotIn("total_count", meta)

    def test_prices_our_products_header_partials_preserve_existing_markup(self):
        actions = (
            BASE_DIR
            / "prices/templates/prices/_our_products_catalog_header_actions.html"
        ).read_text(encoding="utf-8")
        meta = (
            BASE_DIR / "prices/templates/prices/_our_products_catalog_header_meta.html"
        ).read_text(encoding="utf-8")

        self.assertIn("prices:fragrantica_product_review", actions)
        self.assertIn("prices:catalogue_linking_workbench", actions)
        self.assertIn("prices:our_product_concentration_audit", actions)
        self.assertIn("assistant_core:catalog_import", actions)
        self.assertIn("assistant_core:catalog_perfume_create", actions)
        self.assertEqual(actions.count('class="button secondary"'), 4)
        self.assertIn('class="button primary"', actions)
        self.assertIn("total_count", meta)

    def test_prices_supplier_overview_header_actions_preserve_existing_markup(self):
        content = (
            BASE_DIR / "prices/templates/prices/_supplier_overview_header_actions.html"
        ).read_text(encoding="utf-8")

        self.assertIn("prices:import_settings", content)
        self.assertIn("prices:supplier_reimport_all_prices", content)
        self.assertIn("prices:supplier_import_email_all", content)
        self.assertIn("data-email-update-all-form", content)
        self.assertIn("data-email-update-all", content)
        self.assertIn("any_running", content)

    def test_prices_detail_header_partials_preserve_existing_actions(self):
        for template_path in PRICES_DETAIL_HEADER_ACTION_PARTIALS:
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                self.assertIn("button", content)

        import_actions = (
            BASE_DIR / "prices/templates/prices/_import_detail_header_actions.html"
        ).read_text(encoding="utf-8")
        self.assertIn("prices:import_delete", import_actions)
        self.assertIn('name="next"', import_actions)
        self.assertIn("Delete batch", import_actions)

        our_product_actions = (
            BASE_DIR / "prices/templates/prices/_our_product_detail_header_actions.html"
        ).read_text(encoding="utf-8")
        self.assertIn("prices:our_product_update", our_product_actions)
        self.assertIn('aria-label="Edit"', our_product_actions)

        supplier_actions = (
            BASE_DIR / "prices/templates/prices/_supplier_detail_header_actions.html"
        ).read_text(encoding="utf-8")
        self.assertIn("prices:supplier_update", supplier_actions)
        self.assertIn('aria-label="Edit supplier"', supplier_actions)

        product_meta = (
            BASE_DIR / "prices/templates/prices/_product_detail_header_meta.html"
        ).read_text(encoding="utf-8")
        self.assertIn("object.supplier.name|fix_text", product_meta)
        self.assertIn("object.supplier_sku|fix_text", product_meta)

    def test_prices_detail_headers_keep_existing_meta_and_spacing(self):
        expectations = {
            "prices/templates/prices/import_detail.html": [
                'class_name="space-bottom-none"',
                'title_prefix="Import #"',
                'actions_template="prices/_import_detail_header_actions.html"',
            ],
            "prices/templates/prices/our_product_detail.html": [
                'class_name="space-bottom-none"',
                'meta_template="prices/_our_product_detail_header_meta.html"',
                'actions_template="prices/_our_product_detail_header_actions.html"',
            ],
            "prices/templates/prices/supplier_detail.html": [
                'class_name="space-bottom-none"',
                'subtitle="Supplier profile and mappings."',
                'actions_template="prices/_supplier_detail_header_actions.html"',
            ],
            "prices/templates/prices/product_detail.html": [
                'class_name="space-bottom-none"',
                'meta_template="prices/_product_detail_header_meta.html"',
            ],
        }

        for template_path, expected_fragments in expectations.items():
            with self.subTest(template=template_path):
                content = (BASE_DIR / template_path).read_text(encoding="utf-8")

                for expected_fragment in expected_fragments:
                    self.assertIn(expected_fragment, content)
