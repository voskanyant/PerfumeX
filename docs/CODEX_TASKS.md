# Codex Tasks

## Purpose of this document

Living notes for current agent priorities, risks, repeated mistakes, and lessons. Keep entries short and actionable; do not use this as a full changelog.

Related docs: [AGENTS.md](../AGENTS.md), [README.md](../README.md), [docs/REPO_MAP.md](REPO_MAP.md), [docs/DOMAIN_MODEL.md](DOMAIN_MODEL.md), [docs/UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md), [docs/DECISIONS.md](DECISIONS.md), [docs/WORKING_RULES.md](WORKING_RULES.md), [docs/DRIFT_CHECKLIST.md](DRIFT_CHECKLIST.md).

## Current priorities

- Keep repository docs concise and synchronized with real app ownership.
- Prefer database-backed assistant knowledge over hardcoded parser exceptions.
- Improve safety around imports, email processing, normalization, and linking without changing behavior casually.
- Reuse existing UI components/classes before adding new markup or CSS.
- Keep checks realistic: PostgreSQL is required for Django checks/tests.

## Known risks

- `prices/views.py` is now a compatibility export module for focused `prices/views_*.py` modules; avoid adding new workflows there.
- Background import work uses web-process subprocess/thread patterns rather than a dedicated queue.
- RQ/Redis job infrastructure exists; email import board and bulk price reimport actions now enqueue jobs. CBR sync has a worker-callable command, but the currency UI still runs CBR sync in-request to preserve immediate feedback.
- 2026-04-30 - Architecture: generic CRUD view bases, generic record CRUD views, account/user/group views, basic supplier CRUD/detail views, supplier overview views, import-configuration CRUD views, import logs/settings/wizard/detail/delete views, supplier import page/quick upload/price-source views, supplier email action/status views, mapping preview, supplier rate recalculation, bulk price reimport, currency settings views, dashboard/documentation shell views, supplier-product staff/viewer browser and mutation/CRUD views, older product-linking views, legacy `OurProduct`/catalogue variant views, supplier-board status, auto-import scan status, email import run safety, import history timestamps, import log rendering, supplier product filtering/display, cron runner status, currency/rate display logic, and supplier import operations now have service modules; keep `prices/views.py` as view exports only.
- `db.sqlite3` is present but the app is PostgreSQL-only.
- Import and email code has a large data blast radius: supplier products, snapshots, diagnostics, and active/inactive state.
- `SupplierProduct` can link to both legacy `OurProduct` and canonical catalogue models; link changes need care.
- Assistant parsing can regress many rows if one rule or alias is too broad.
- Local `run_python_server.cmd` uses `--noreload`; stale server processes can hide template/Python changes.
- 2026-04-30 - Checks: DB-backed Django tests need valid local PostgreSQL credentials; without them, test DB creation fails before tests run.
- 2026-05-01 - Checks: `npm run lint:js` runs cleanly after `npm install` and covers `prices` plus `assistant_linking` static JS; `python scripts/check_local_smoke.py` includes it when `node_modules` is present and keeps `python scripts/check_js_syntax.py` as the dependency-light fallback; `python scripts/check_js_syntax_rules.py` protects JS file discovery.
- 2026-05-01 - Checks: `python scripts/check_js_dom_safety.py` blocks direct HTML injection APIs in static JavaScript and inline template scripts; `python scripts/check_js_dom_safety_rules.py` protects the checker rules.
- 2026-05-01 - Checks: `python scripts/check_js_accessibility.py` requires generated checkbox/radio controls in static JavaScript to have accessible labels or label wrappers; `python scripts/check_js_accessibility_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_js_table_labels.py` requires generated table data cells in static JavaScript to set `data-label` or `colSpan`; `python scripts/check_js_table_labels_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_agent_docs.py` protects required repo-memory docs, purpose sections, AGENTS protocol anchors, focused-doc links, the AGENTS targeted command list, drift-checklist Makefile targets, and CONTRIBUTING Makefile targets; `python scripts/check_agent_docs_rules.py` protects the checker rules.
- 2026-05-01 - Checks: `python scripts/check_markdown_links.py` validates local Markdown links in root, `docs/`, and `assistant_linking/docs/` docs; `python scripts/check_markdown_links_rules.py` protects link parser behavior.
- 2026-05-01 - Checks: `python scripts/check_make_targets.py` keeps every `scripts/check_*.py` file exposed through a matching Makefile target and matching checker/rule script pair; `python scripts/check_make_targets_rules.py` protects the checker rules.
- 2026-05-01 - Checks: `python scripts/check_local_smoke_rules.py` verifies local smoke includes focused checker scripts exposed through Makefile targets.
- 2026-05-01 - Checks: `python scripts/check_secret_patterns.py` scans changed text files for obvious committed credentials, including structured `key: value` config syntax; `python scripts/check_secret_patterns_rules.py` protects the checker rules.
- 2026-05-01 - Checks: `python scripts/check_python_syntax.py` compiles project/app/script Python files and catches syntax errors outside Django import paths; `python scripts/check_python_syntax_rules.py` protects the checker rules.
- 2026-05-01 - Checks: `python scripts/check_migration_graph.py` validates migration conflicts and dependencies from disk without requiring PostgreSQL; `python scripts/check_migration_graph_rules.py` protects the checker rules.
- 2026-05-01 - Checks: `python scripts/check_management_commands.py` imports local management command modules without running command logic; `python scripts/check_management_commands_rules.py` protects discovery rules.
- 2026-05-01 - Checks: `python scripts/check_service_imports.py` imports local service modules without running workflows; `python scripts/check_service_imports_rules.py` protects discovery rules.
- 2026-05-01 - Checks: `python scripts/check_urls.py` loads root URL configuration and blocks new duplicate un-namespaced route names; `python scripts/check_urls_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_layout.py` requires full-page templates to extend `prices/base.html` and use the shared page-header/breadcrumb pattern while allowing base, include, component, and underscore partials; `python scripts/check_template_layout_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_css_static.py` checks static CSS for merge markers, balanced braces, negative letter spacing, and viewport-scaled font sizes; `python scripts/check_css_static_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_accessibility.py` requires icon-only `.button.icon` actions to include `title` and `aria-label`, image tags to include `alt`, checkbox/radio controls to have accessible labels, and text/search inputs to have accessible labels; `python scripts/check_template_accessibility_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_buttons.py` requires template buttons to declare explicit valid types; `python scripts/check_template_buttons_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_ids.py` requires literal template ids to be unique within each template; `python scripts/check_template_ids_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_table_mobile.py` requires `table-mobile` data cells to have `data-label` or `colspan`; `python scripts/check_table_mobile_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_table_headers.py` requires template table header cells to declare `scope` and empty headers to have an accessible name; `python scripts/check_table_headers_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_csrf.py` requires literal POST forms in templates to include `{% csrf_token %}`; `python scripts/check_template_csrf_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_drawers.py` requires drawers and native dialogs to keep accessible control/label markup; `python scripts/check_template_drawers_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_labels.py` verifies label targets match literal ids or Django-rendered form fields; `python scripts/check_template_labels_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_inline_styles.py` blocks template `style` attributes and `<style>` blocks so visual rules stay in static CSS; `python scripts/check_template_inline_styles_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_template_links.py` blocks `javascript:` hrefs and requires `target="_blank"` links to include `rel="noopener"`; `python scripts/check_template_links_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_destructive_actions.py` requires destructive POST controls to include `data-confirm` and delete confirmation links to use danger button styling; `python scripts/check_destructive_actions_rules.py` protects checker rules.
- 2026-05-01 - Checks: `python scripts/check_templates.py`, `python scripts/check_template_urls.py`, and `python scripts/check_static_references.py` protect template compilation, literal URL names, and static references; their `*_rules.py` scripts protect checker parsing/discovery rules.
- 2026-05-01 - Checks: `python scripts/check_view_exports.py` runs app view-boundary suites; `python scripts/check_view_exports_rules.py` protects the wrapper target list and command shape.
- 2026-05-01 - Checks: `python scripts/check_ui_partials.py` runs shared UI include/template boundary suites; `python scripts/check_ui_partials_rules.py` protects the wrapper target list and command shape.
- 2026-05-01 - Checks: `python scripts/check_doc_drift.py` includes management command changes in ownership/domain review warnings, with import/email/rate/link-related commands treated as business-sensitive.
- 2026-05-02 - Performance: assistant queue/list pages must not run expensive parse/reparse work on normal GET loads against live-sized data; make visible-row refresh actions explicit.
- 2026-05-02 - Operations: local catalogue/Fragrantica database edits do not deploy through Git. Promote reviewed Fragrantica links with the JSON export/import commands and dry-run on live before `--apply`.
- 2026-05-03 - Operations: parser/KB migrations do not update already-saved production parses unless `PARSER_VERSION` changes and affected rows are reparsed. Do not run broad/full-table production reparses inside deploy; use exact affected name patterns or a separate background/maintenance job.
- 2026-05-02 - Operations: heavy Fragrantica staging imports should run on the server with the folder import command, using parsed JSON exports when available to preserve year, audience/gender, and source URLs. Always dry-run first, then run with `--apply` after checking counts.
- 2026-05-02 - Assistant normalization: Unparsed means no saved `ParsedSupplierProduct`; temporary parser previews may help operators inspect visible rows but must not move rows between queues. Use explicit parse jobs/actions to create saved parses, then handle catalogue/link evidence separately.
- 2026-05-02 - Deploy checks: `make local-smoke` is not enough before pushing to `main`. Run `make deploy-gate` for the fast deploy gate; add `DEPLOY_GATE_ARGS="--ui --test ..."` for UI or focused behavior changes, and use `make deploy-gate-full` before high-risk merges or shared parser/linking/schema/import changes.
- 2026-05-03 - Deploy performance: push-to-main CI uses the fast deploy gate by default; full Django tests remain for pull requests and explicit full gates. Production deploy should skip unchanged requirements, static collection, normalization stats, and reparses unless explicitly requested.
- 2026-05-03 - Catalogue linking: Fragrantica <-> Our Products review should extend the shared `catalogue_linking_workbench` two-column workflow instead of adding another one-off link button/list page.
- 2026-05-03 - Operations: after all old supplier price folders are prepared, uploaded, and imported, run a final supplier-history reconciliation phase: re-check old emails with current import logic, merge uploaded prepared files with valid email/backfill files, preserve cross-day duplicate price files as separate history, skip only same-day exact duplicates, respect supplier-specific layout/currency rules, validate SKU/article identities, missing dates, duplicate snapshots, and price spikes, and clean loose non-price media only after content/import validation proves the file cannot be a price file.

## Repeated mistakes to avoid

- Do not add one-off parser code for one supplier typo, one collection, or one concentration phrase.
- Do not invent new UI styles when `prices/base.html`, shared components, and CSS utilities already cover the pattern.
- Do not assume SQLite works locally.
- Do not treat staged Fragrantica rows as canonical catalogue products.
- Do not silently overwrite assistant/manual links without explicit staff intent and audit trail.
- Do not leave final summaries without code/docs/tests/follow-up fields.
- Do not treat failed deploys, failed checks, parser misses, bad link suggestions, or UI regressions as isolated incidents; extract the reusable cause and add a rule, KB seed, test, checker, or doc note when it can prevent the same error later.

## Lessons learned

- Existing docs are useful but large; new agent docs should summarize and link instead of copying them.
- `assistant_linking/docs/assistant_learning_design.md` is the source of truth for assistant learning discipline.
- The canonical catalogue lives in `catalog`; supplier offers and import history live in `prices`.
- Shared UI components already exist for page headers, tabs, pagination, and table empty states.

## Future note format

Add future notes under the appropriate section using this exact format:

```text
- YYYY-MM-DD - Area: concise lesson/risk/priority. Source: task, bug, or user correction.
```

Only add notes that should affect future work.
