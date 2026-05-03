.PHONY: agent-docs-rules agent-docs-smoke ci deploy-gate lint markdown-link-rules markdown-link-smoke make-target-rules make-target-smoke migration-graph-rules migration-graph-smoke migrations test security command-rules command-smoke css-rules css-smoke destructive-action-rules destructive-action-smoke doc-drift doc-drift-rules js-a11y js-a11y-rules js-dom-safety js-dom-safety-rules js-rules js-smoke js-table-labels js-table-labels-rules python-rules python-smoke secret-rules secret-smoke service-rules service-smoke static-ref-rules static-ref-smoke template-a11y-rules template-a11y-smoke template-button-rules template-button-smoke template-drawer-rules template-drawer-smoke template-id-rules template-id-smoke template-inline-style-rules template-inline-style-smoke table-header-rules table-header-smoke table-mobile-rules table-mobile-smoke template-csrf-rules template-csrf-smoke template-label-rules template-label-smoke template-link-rules template-link-smoke template-rules template-smoke template-layout-rules template-layout-smoke template-url-rules template-url-smoke url-rules url-smoke view-export-rules view-export-smoke ui-partial-rules ui-partial-smoke local-smoke-rules local-smoke

export DEBUG ?= 1
export SECRET_KEY ?= local-ci-not-secret-8f6a90e4d2b64782a4d4c3b2452dd6d7f7e83bb164f24c86
export FERNET_KEYS ?= local-ci-fernet-key-8f6a90e4d2b64782a4d4c3b2452dd6d7
export DATABASE_ENGINE ?= postgres
export POSTGRES_HOST ?= 127.0.0.1
export POSTGRES_PORT ?= 5432
export POSTGRES_DB ?= perfumex_local
export POSTGRES_USER ?= postgres
export POSTGRES_PASSWORD ?=
export ALLOWED_HOSTS ?= 127.0.0.1,localhost
export CSRF_TRUSTED_ORIGINS ?= https://127.0.0.1,https://localhost
export ASSISTANT_USE_OPENAI ?= false

ci: lint migrations test security

deploy-gate:
	python scripts/deploy_gate.py

lint:
	ruff check .
	black --check .
	djlint --check prices/templates assistant_core/templates assistant_linking/templates
	npm run lint:js

migrations:
	python manage.py makemigrations --check --dry-run
	python manage.py migrate --plan

test:
	python manage.py test --parallel=4 --verbosity=1 --noinput

security:
	pip-audit --strict -r requirements.txt
	python manage.py check --deploy
	bandit -r prices assistant_core assistant_linking catalog --severity-level high --exclude "*/tests.py,*/tests/*"

command-smoke:
	python scripts/check_management_commands.py

command-rules:
	python scripts/check_management_commands_rules.py

css-smoke:
	python scripts/check_css_static.py

css-rules:
	python scripts/check_css_static_rules.py

agent-docs-smoke:
	python scripts/check_agent_docs.py

agent-docs-rules:
	python scripts/check_agent_docs_rules.py

markdown-link-smoke:
	python scripts/check_markdown_links.py

markdown-link-rules:
	python scripts/check_markdown_links_rules.py

make-target-smoke:
	python scripts/check_make_targets.py

make-target-rules:
	python scripts/check_make_targets_rules.py

local-smoke-rules:
	python scripts/check_local_smoke_rules.py

migration-graph-smoke:
	python scripts/check_migration_graph.py

migration-graph-rules:
	python scripts/check_migration_graph_rules.py

doc-drift:
	python scripts/check_doc_drift.py

doc-drift-rules:
	python scripts/check_doc_drift_rules.py

destructive-action-smoke:
	python scripts/check_destructive_actions.py

destructive-action-rules:
	python scripts/check_destructive_actions_rules.py

js-smoke:
	python scripts/check_js_syntax.py

js-rules:
	python scripts/check_js_syntax_rules.py

js-dom-safety:
	python scripts/check_js_dom_safety.py

js-dom-safety-rules:
	python scripts/check_js_dom_safety_rules.py

js-a11y:
	python scripts/check_js_accessibility.py

js-a11y-rules:
	python scripts/check_js_accessibility_rules.py

js-table-labels:
	python scripts/check_js_table_labels.py

js-table-labels-rules:
	python scripts/check_js_table_labels_rules.py

python-smoke:
	python scripts/check_python_syntax.py

python-rules:
	python scripts/check_python_syntax_rules.py

secret-smoke:
	python scripts/check_secret_patterns.py

secret-rules:
	python scripts/check_secret_patterns_rules.py

service-smoke:
	python scripts/check_service_imports.py

service-rules:
	python scripts/check_service_imports_rules.py

static-ref-smoke:
	python scripts/check_static_references.py

static-ref-rules:
	python scripts/check_static_references_rules.py

table-mobile-smoke:
	python scripts/check_table_mobile.py

table-mobile-rules:
	python scripts/check_table_mobile_rules.py

table-header-smoke:
	python scripts/check_table_headers.py

table-header-rules:
	python scripts/check_table_headers_rules.py

template-a11y-smoke:
	python scripts/check_template_accessibility.py

template-a11y-rules:
	python scripts/check_template_accessibility_rules.py

template-button-smoke:
	python scripts/check_template_buttons.py

template-button-rules:
	python scripts/check_template_buttons_rules.py

template-id-smoke:
	python scripts/check_template_ids.py

template-id-rules:
	python scripts/check_template_ids_rules.py

template-csrf-smoke:
	python scripts/check_template_csrf.py

template-csrf-rules:
	python scripts/check_template_csrf_rules.py

template-drawer-smoke:
	python scripts/check_template_drawers.py

template-drawer-rules:
	python scripts/check_template_drawers_rules.py

template-label-smoke:
	python scripts/check_template_labels.py

template-label-rules:
	python scripts/check_template_labels_rules.py

template-inline-style-smoke:
	python scripts/check_template_inline_styles.py

template-inline-style-rules:
	python scripts/check_template_inline_styles_rules.py

template-link-smoke:
	python scripts/check_template_links.py

template-link-rules:
	python scripts/check_template_links_rules.py

template-smoke:
	python scripts/check_templates.py

template-rules:
	python scripts/check_templates_rules.py

template-layout-smoke:
	python scripts/check_template_layout.py

template-layout-rules:
	python scripts/check_template_layout_rules.py

template-url-smoke:
	python scripts/check_template_urls.py

template-url-rules:
	python scripts/check_template_urls_rules.py

url-smoke:
	python scripts/check_urls.py

url-rules:
	python scripts/check_urls_rules.py

view-export-smoke:
	python scripts/check_view_exports.py

view-export-rules:
	python scripts/check_view_exports_rules.py

ui-partial-smoke:
	python scripts/check_ui_partials.py

ui-partial-rules:
	python scripts/check_ui_partials_rules.py

local-smoke:
	python scripts/check_local_smoke.py
