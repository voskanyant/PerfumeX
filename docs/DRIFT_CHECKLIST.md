# Drift Checklist

## Purpose of this document

Final review checklist for any feature, fix, or documentation change. Use this before the final task summary to catch doc drift, ownership drift, missed checks, and repeated corrections.

Related docs: [AGENTS.md](../AGENTS.md), [README.md](../README.md), [docs/WORKING_RULES.md](WORKING_RULES.md), [docs/REPO_MAP.md](REPO_MAP.md), [docs/DOMAIN_MODEL.md](DOMAIN_MODEL.md), [docs/UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md), [docs/DECISIONS.md](DECISIONS.md), [docs/CODEX_TASKS.md](CODEX_TASKS.md).

## Final review

- Did code change?
- Did UI/templates/static assets change?
- Did business behavior change?
- Did imports, parsing, linking, or assistant knowledge behavior change?
- Did models or migrations change?
- Did docs need updating?
- Did tests/checks run?
- Did `python scripts/check_doc_drift.py` or `make doc-drift` run when code, UI, or agent docs changed?
- Did this task reveal a repeated correction that should become a rule?
- Did the final summary include:
  - Code changed
  - Docs changed
  - Tests/checks run
  - Follow-up notes

## Doc drift check

Run this lightweight warning-only check before finishing feature work:

```bash
python scripts/check_doc_drift.py
make doc-drift
```

The script inspects changed and untracked files in Git. It warns when likely documentation companions were not touched, but it exits successfully so it does not block work. Treat warnings as prompts to decide whether docs need an update, not as automatic failures.

When changing the drift checker itself, run its rule smoke tests:

```bash
python scripts/check_doc_drift_rules.py
make doc-drift-rules
```

When changing `AGENTS.md` or focused repo-memory docs, run:

```bash
python scripts/check_agent_docs.py
make agent-docs-smoke
```

When changing the agent-doc checker itself, run:

```bash
python scripts/check_agent_docs_rules.py
make agent-docs-rules
```

When changing Markdown documentation links, run:

```bash
python scripts/check_markdown_links.py
make markdown-link-smoke
```

When changing the Markdown link checker itself, run:

```bash
python scripts/check_markdown_links_rules.py
make markdown-link-rules
```

When changing the Makefile smoke/check target surface, run:

```bash
python scripts/check_make_targets.py
make make-target-smoke
```

When changing the Makefile target checker itself, run:

```bash
python scripts/check_make_targets_rules.py
make make-target-rules
```

For a cross-platform local smoke pass that avoids full DB-backed tests, run:

```bash
python scripts/check_local_smoke.py
make local-smoke
```

`check_local_smoke.py` always runs the dependency-light JavaScript syntax check. If `node_modules` is installed, it also runs `npm run lint:js`.

When changing the local smoke runner itself, run:

```bash
python scripts/check_local_smoke_rules.py
make local-smoke-rules
```

When changing Python files that may not be imported by Django checks, run:

```bash
python scripts/check_python_syntax.py
make python-smoke
```

When changing the Python syntax checker itself, run:

```bash
python scripts/check_python_syntax_rules.py
make python-rules
```

When changing migrations or migration dependencies, run:

```bash
python scripts/check_migration_graph.py
make migration-graph-smoke
```

When changing the migration graph checker itself, run:

```bash
python scripts/check_migration_graph_rules.py
make migration-graph-rules
```

When changing static JavaScript and `npm run lint:js` is unavailable locally, run:

```bash
python scripts/check_js_syntax.py
make js-smoke
```

When changing the JavaScript syntax checker itself, run:

```bash
python scripts/check_js_syntax_rules.py
make js-rules
```

When changing JavaScript or inline template scripts that render backend data, run:

```bash
python scripts/check_js_dom_safety.py
make js-dom-safety
```

When changing the JavaScript DOM safety checker itself, run:

```bash
python scripts/check_js_dom_safety_rules.py
make js-dom-safety-rules
```

When changing JavaScript that generates checkbox or radio controls, run:

```bash
python scripts/check_js_accessibility.py
make js-a11y
```

When changing the JavaScript accessibility checker itself, run:

```bash
python scripts/check_js_accessibility_rules.py
make js-a11y-rules
```

When changing JavaScript that generates table rows or data cells, run:

```bash
python scripts/check_js_table_labels.py
make js-table-labels
```

When changing the JavaScript table-label checker itself, run:

```bash
python scripts/check_js_table_labels_rules.py
make js-table-labels-rules
```

When changing files that may contain credentials, or before sharing a branch, run:

```bash
python scripts/check_secret_patterns.py
make secret-smoke
```

When changing the secret-pattern checker itself, run:

```bash
python scripts/check_secret_patterns_rules.py
make secret-rules
```

When changing service modules or extracting logic into services, run:

```bash
python scripts/check_service_imports.py
make service-smoke
```

When changing the service import checker itself, run:

```bash
python scripts/check_service_imports_rules.py
make service-rules
```

When changing management commands or the services they import, run:

```bash
python scripts/check_management_commands.py
make command-smoke
```

When changing management command discovery checks, run:

```bash
python scripts/check_management_commands_rules.py
make command-rules
```

When changing Django templates, run:

```bash
python scripts/check_templates.py
make template-smoke
```

When changing the template compile checker, run:

```bash
python scripts/check_templates_rules.py
make template-rules
```

When changing full-page templates, page-header placement, or template shell/layout conventions, run:

```bash
python scripts/check_template_layout.py
make template-layout-smoke
```

When changing the template layout checker, run:

```bash
python scripts/check_template_layout_rules.py
make template-layout-rules
```

When changing URL names or template `{% url %}` references, run:

```bash
python scripts/check_template_urls.py
make template-url-smoke
```

When changing the template URL checker, run:

```bash
python scripts/check_template_urls_rules.py
make template-url-rules
```

When changing root/app URL configuration, run:

```bash
python scripts/check_urls.py
make url-smoke
```

When changing the URL configuration checker itself, run:

```bash
python scripts/check_urls_rules.py
make url-rules
```

When changing template static asset references, run:

```bash
python scripts/check_static_references.py
make static-ref-smoke
```

When changing the static-reference checker, run:

```bash
python scripts/check_static_references_rules.py
make static-ref-rules
```

When changing icon-only actions, image tags, checkbox/radio controls, or text/search inputs in templates, run:

```bash
python scripts/check_template_accessibility.py
make template-a11y-smoke
```

When changing the template accessibility checker itself, run:

```bash
python scripts/check_template_accessibility_rules.py
make template-a11y-rules
```

When changing template buttons, run:

```bash
python scripts/check_template_buttons.py
make template-button-smoke
```

When changing the template button checker itself, run:

```bash
python scripts/check_template_buttons_rules.py
make template-button-rules
```

When changing template ids or JavaScript hooks that reference controls, run:

```bash
python scripts/check_template_ids.py
make template-id-smoke
```

When changing the template id checker itself, run:

```bash
python scripts/check_template_ids_rules.py
make template-id-rules
```

When changing static CSS, run:

```bash
python scripts/check_css_static.py
make css-smoke
```

When changing the CSS rule checker, run:

```bash
python scripts/check_css_static_rules.py
make css-rules
```

When changing responsive table markup, run:

```bash
python scripts/check_table_mobile.py
make table-mobile-smoke
```

When changing the mobile table checker itself, run:

```bash
python scripts/check_table_mobile_rules.py
make table-mobile-rules
```

When changing table headers in templates, run:

```bash
python scripts/check_table_headers.py
make table-header-smoke
```

When changing the table-header checker itself, run:

```bash
python scripts/check_table_headers_rules.py
make table-header-rules
```

When changing POST forms in templates, run:

```bash
python scripts/check_template_csrf.py
make template-csrf-smoke
```

When changing the template CSRF checker itself, run:

```bash
python scripts/check_template_csrf_rules.py
make template-csrf-rules
```

When changing drawers or native dialogs in templates, run:

```bash
python scripts/check_template_drawers.py
make template-drawer-smoke
```

When changing the template drawer/dialog checker itself, run:

```bash
python scripts/check_template_drawers_rules.py
make template-drawer-rules
```

When changing template visual styling, run:

```bash
python scripts/check_template_inline_styles.py
make template-inline-style-smoke
```

When changing the template inline-style checker itself, run:

```bash
python scripts/check_template_inline_styles_rules.py
make template-inline-style-rules
```

When changing labels or form control ids in templates, run:

```bash
python scripts/check_template_labels.py
make template-label-smoke
```

When changing the template label checker itself, run:

```bash
python scripts/check_template_labels_rules.py
make template-label-rules
```

When changing links in templates, run:

```bash
python scripts/check_template_links.py
make template-link-smoke
```

When changing the template link checker itself, run:

```bash
python scripts/check_template_links_rules.py
make template-link-rules
```

When changing destructive POST actions in templates, run:

```bash
python scripts/check_destructive_actions.py
make destructive-action-smoke
```

When changing the destructive-action checker itself, run:

```bash
python scripts/check_destructive_actions_rules.py
make destructive-action-rules
```

When changing shared UI includes or component markup, run:

```bash
python scripts/check_ui_partials.py
make ui-partial-smoke
```

When changing the UI partial checker wrapper itself, run:

```bash
python scripts/check_ui_partials_rules.py
make ui-partial-rules
```

When splitting view modules or changing URL/view export surfaces, run:

```bash
python scripts/check_view_exports.py
make view-export-smoke
```

When changing the view export checker wrapper itself, run:

```bash
python scripts/check_view_exports_rules.py
make view-export-rules
```

## If code changed

- Is the change in the owning app from `docs/REPO_MAP.md`?
- Is the blast radius narrow?
- Are existing patterns reused?
- Are data mutations transactional/auditable where needed?
- Are unrelated local changes preserved?

## If UI changed

- Did you read `docs/UI_DESIGN_SYSTEM.md`?
- Did you reuse existing layout, buttons, tabs, tables, filters, forms, empty states, and pagination?
- Does the UI still work on desktop and mobile patterns used by the app?
- Did you avoid a new visual style?

## If business behavior changed

- Did you read `docs/DOMAIN_MODEL.md`?
- Did you avoid confusing supplier products, internal products, catalogue perfumes, variants, aliases, and staged external catalogue rows?
- Did you prefer data/rules/aliases over one-off hardcoded business fixes?
- Did you update `docs/DECISIONS.md` if the change creates a durable decision?

## If docs changed

- Are docs short and practical?
- Do they link to large existing docs instead of duplicating them?
- Are they accurate to the current code?
- Did you avoid documenting temporary implementation details as permanent rules?
