.PHONY: agent-docs-rules agent-docs-smoke ci deploy-gate deploy-gate-full format-touched lint lint-touched markdown-link-rules markdown-link-smoke make-target-rules make-target-smoke migration-graph-rules migration-graph-smoke migrations test security command-rules command-smoke css-rules css-smoke destructive-action-rules destructive-action-smoke doc-drift doc-drift-rules js-a11y js-a11y-rules js-dom-safety js-dom-safety-rules js-rules js-smoke js-table-labels js-table-labels-rules python-rules python-smoke secret-rules secret-smoke service-rules service-smoke static-ref-rules static-ref-smoke template-a11y-rules template-a11y-smoke template-button-rules template-button-smoke template-drawer-rules template-drawer-smoke template-id-rules template-id-smoke template-inline-style-rules template-inline-style-smoke table-header-rules table-header-smoke table-mobile-rules table-mobile-smoke template-csrf-rules template-csrf-smoke template-label-rules template-label-smoke template-link-rules template-link-smoke template-rules template-smoke template-layout-rules template-layout-smoke template-url-rules template-url-smoke url-rules url-smoke view-export-rules view-export-smoke ui-partial-rules ui-partial-smoke local-smoke-rules local-smoke ui-smoke

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
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then printf '%s' .venv/bin/python; elif command -v python3 >/dev/null 2>&1; then printf '%s' python3; else printf '%s' python; fi)

ci: lint migrations test security

deploy-gate:
	$(PYTHON) scripts/deploy_gate.py $(DEPLOY_GATE_ARGS)

deploy-gate-full:
	$(PYTHON) scripts/deploy_gate.py --full

ui-smoke:
	$(MAKE) template-smoke template-layout-smoke template-a11y-smoke \
		template-button-smoke template-id-smoke template-csrf-smoke \
		template-drawer-smoke template-label-smoke \
		template-inline-style-smoke template-link-smoke \
		table-mobile-smoke table-header-smoke css-smoke static-ref-smoke \
		ui-partial-smoke js-smoke js-dom-safety js-a11y js-table-labels

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m black --check .
	$(PYTHON) -m djlint --check prices/templates assistant_core/templates assistant_linking/templates
	npm run lint:js

lint-touched:
	@tracked=$$(git diff --name-only --diff-filter=ACMRTUXB HEAD -- '*.py'); \
	untracked=$$(git ls-files --others --exclude-standard -- '*.py'); \
	files=$$(printf '%s\n%s\n' "$$tracked" "$$untracked" | sed '/^$$/d' | sort -u); \
	if [ -z "$$files" ]; then \
		echo "No changed Python files to check."; \
	else \
		$(PYTHON) -m ruff check $$files && $(PYTHON) -m black --check $$files; \
	fi

format-touched:
	@tracked=$$(git diff --name-only --diff-filter=ACMRTUXB HEAD -- '*.py'); \
	untracked=$$(git ls-files --others --exclude-standard -- '*.py'); \
	files=$$(printf '%s\n%s\n' "$$tracked" "$$untracked" | sed '/^$$/d' | sort -u); \
	if [ -z "$$files" ]; then \
		echo "No changed Python files to format."; \
	else \
		$(PYTHON) -m black $$files; \
	fi

migrations:
	$(PYTHON) manage.py makemigrations --check --dry-run
	$(PYTHON) manage.py migrate --plan

test:
	$(PYTHON) manage.py test --parallel=4 --verbosity=1 --noinput

security:
	$(PYTHON) -m pip_audit --strict -r requirements.txt
	$(PYTHON) manage.py check --deploy
	$(PYTHON) -m bandit -r prices assistant_core assistant_linking catalog --severity-level high --exclude "*/tests.py,*/tests/*"

command-smoke:
	$(PYTHON) scripts/check_management_commands.py

command-rules:
	$(PYTHON) scripts/check_management_commands_rules.py

css-smoke:
	$(PYTHON) scripts/check_css_static.py

css-rules:
	$(PYTHON) scripts/check_css_static_rules.py

agent-docs-smoke:
	$(PYTHON) scripts/check_agent_docs.py

agent-docs-rules:
	$(PYTHON) scripts/check_agent_docs_rules.py

markdown-link-smoke:
	$(PYTHON) scripts/check_markdown_links.py

markdown-link-rules:
	$(PYTHON) scripts/check_markdown_links_rules.py

make-target-smoke:
	$(PYTHON) scripts/check_make_targets.py

make-target-rules:
	$(PYTHON) scripts/check_make_targets_rules.py

local-smoke-rules:
	$(PYTHON) scripts/check_local_smoke_rules.py

migration-graph-smoke:
	$(PYTHON) scripts/check_migration_graph.py

migration-graph-rules:
	$(PYTHON) scripts/check_migration_graph_rules.py

doc-drift:
	$(PYTHON) scripts/check_doc_drift.py

doc-drift-rules:
	$(PYTHON) scripts/check_doc_drift_rules.py

destructive-action-smoke:
	$(PYTHON) scripts/check_destructive_actions.py

destructive-action-rules:
	$(PYTHON) scripts/check_destructive_actions_rules.py

js-smoke:
	$(PYTHON) scripts/check_js_syntax.py

js-rules:
	$(PYTHON) scripts/check_js_syntax_rules.py

js-dom-safety:
	$(PYTHON) scripts/check_js_dom_safety.py

js-dom-safety-rules:
	$(PYTHON) scripts/check_js_dom_safety_rules.py

js-a11y:
	$(PYTHON) scripts/check_js_accessibility.py

js-a11y-rules:
	$(PYTHON) scripts/check_js_accessibility_rules.py

js-table-labels:
	$(PYTHON) scripts/check_js_table_labels.py

js-table-labels-rules:
	$(PYTHON) scripts/check_js_table_labels_rules.py

python-smoke:
	$(PYTHON) scripts/check_python_syntax.py

python-rules:
	$(PYTHON) scripts/check_python_syntax_rules.py

secret-smoke:
	$(PYTHON) scripts/check_secret_patterns.py

secret-rules:
	$(PYTHON) scripts/check_secret_patterns_rules.py

service-smoke:
	$(PYTHON) scripts/check_service_imports.py

service-rules:
	$(PYTHON) scripts/check_service_imports_rules.py

static-ref-smoke:
	$(PYTHON) scripts/check_static_references.py

static-ref-rules:
	$(PYTHON) scripts/check_static_references_rules.py

table-mobile-smoke:
	$(PYTHON) scripts/check_table_mobile.py

table-mobile-rules:
	$(PYTHON) scripts/check_table_mobile_rules.py

table-header-smoke:
	$(PYTHON) scripts/check_table_headers.py

table-header-rules:
	$(PYTHON) scripts/check_table_headers_rules.py

template-a11y-smoke:
	$(PYTHON) scripts/check_template_accessibility.py

template-a11y-rules:
	$(PYTHON) scripts/check_template_accessibility_rules.py

template-button-smoke:
	$(PYTHON) scripts/check_template_buttons.py

template-button-rules:
	$(PYTHON) scripts/check_template_buttons_rules.py

template-id-smoke:
	$(PYTHON) scripts/check_template_ids.py

template-id-rules:
	$(PYTHON) scripts/check_template_ids_rules.py

template-csrf-smoke:
	$(PYTHON) scripts/check_template_csrf.py

template-csrf-rules:
	$(PYTHON) scripts/check_template_csrf_rules.py

template-drawer-smoke:
	$(PYTHON) scripts/check_template_drawers.py

template-drawer-rules:
	$(PYTHON) scripts/check_template_drawers_rules.py

template-label-smoke:
	$(PYTHON) scripts/check_template_labels.py

template-label-rules:
	$(PYTHON) scripts/check_template_labels_rules.py

template-inline-style-smoke:
	$(PYTHON) scripts/check_template_inline_styles.py

template-inline-style-rules:
	$(PYTHON) scripts/check_template_inline_styles_rules.py

template-link-smoke:
	$(PYTHON) scripts/check_template_links.py

template-link-rules:
	$(PYTHON) scripts/check_template_links_rules.py

template-smoke:
	$(PYTHON) scripts/check_templates.py

template-rules:
	$(PYTHON) scripts/check_templates_rules.py

template-layout-smoke:
	$(PYTHON) scripts/check_template_layout.py

template-layout-rules:
	$(PYTHON) scripts/check_template_layout_rules.py

template-url-smoke:
	$(PYTHON) scripts/check_template_urls.py

template-url-rules:
	$(PYTHON) scripts/check_template_urls_rules.py

url-smoke:
	$(PYTHON) scripts/check_urls.py

url-rules:
	$(PYTHON) scripts/check_urls_rules.py

view-export-smoke:
	$(PYTHON) scripts/check_view_exports.py

view-export-rules:
	$(PYTHON) scripts/check_view_exports_rules.py

ui-partial-smoke:
	$(PYTHON) scripts/check_ui_partials.py

ui-partial-rules:
	$(PYTHON) scripts/check_ui_partials_rules.py

local-smoke:
	$(PYTHON) scripts/check_local_smoke.py
