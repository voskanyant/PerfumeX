# Codex Tasks

## Purpose of this document

Living notes for current agent priorities, risks, repeated mistakes, and lessons. Keep entries short and actionable; do not use this as a full changelog.

Related docs: [AGENTS.md](../AGENTS.md), [README.md](../README.md), [docs/REPO_MAP.md](REPO_MAP.md), [docs/DOMAIN_MODEL.md](DOMAIN_MODEL.md), [docs/UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md), [docs/DECISIONS.md](DECISIONS.md), [docs/WORKING_RULES.md](WORKING_RULES.md), [docs/DRIFT_CHECKLIST.md](DRIFT_CHECKLIST.md).

## Current priorities

- Keep repository docs concise and synchronized with real app ownership.
- Prefer database-backed assistant knowledge over hardcoded parser exceptions.
- Improve safety around imports, email processing, normalization, and linking without changing behavior casually.
- 2026-05-07 - Architecture: service cleanup should move behavior-preserving pure helpers out of large hotspot services first, keeping compatibility imports until call sites can migrate safely.
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
- 2026-05-07 - Checks: `make lint` is venv-aware, but repo-wide `black --check .` currently reports legacy formatting drift. Do not mass-format during unrelated tiny slices; run `make lint-touched` for focused Black/Ruff checks on touched Python files, use `make format-touched` to Black-format only those files, and keep deploy gates green until a dedicated formatting cleanup is planned.
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
- 2026-05-04 - Performance: unparsed normalization previews are expensive because they run parser logic per visible row; keep them behind an explicit preview/refresh action and never attach parser previews during normal list GET loads.
- 2026-05-02 - Operations: local catalogue/Fragrantica database edits do not deploy through Git. Promote reviewed Fragrantica links with the JSON export/import commands and dry-run on live before `--apply`.
- 2026-05-03 - Operations: parser/KB migrations do not update already-saved production parses unless `PARSER_VERSION` changes and affected rows are reparsed. Do not run broad/full-table production reparses inside deploy; use exact affected name patterns or a separate background/maintenance job.
- 2026-05-04 - Operations: normalization detail pages must not display stale unlocked saved parses after parser-version changes; refresh stale unlocked parses on detail load, but preserve human-locked parses and keep never-parsed rows preview-only.
- 2026-05-06 - Operations: normalization queue/list pages read saved parses for speed; after parser-version or KB fixes, use the stale-parse refresh action or visible-row refresh to update saved rows instead of opening products one by one.
- 2026-05-16 - Operations: the Complete parsed products page must not show a stale saved parse that the detail page would immediately correct. Refresh visible unlocked stale rows on that page; keep human-locked stale rows untouched.
- 2026-05-16 - Performance: Complete parsed visible stale refresh should be lightweight on normal GET loads: re-save visible unlocked stale parses without per-row catalogue-conflict candidate matching or stats invalidation. Full parse saves and explicit refresh paths can still run the heavier review logic.
- 2026-05-07 - Operations: complete parsed normalization pages must use stable supplier/product ordering, not `updated_at`, because visible-row refresh updates parse timestamps and otherwise moves rows away before operators can inspect the result.
- 2026-05-18 - Performance: Complete parsed list ordering should follow an indexed deterministic key (`supplier_product_id`, `pk`) instead of joined supplier/name sorting. Large live queues must return first rows through an index path; use search for specific supplier/name lookup.
- 2026-05-19 - Performance: Complete parsed pages must read a stored/indexed queue flag instead of recalculating the complete-parse predicate on every GET. Maintain `ParsedSupplierProduct.is_complete_parse` on save and backfill it in migrations before relying on it in list queries.
- 2026-05-19 - Performance: Large operator list pages must render the first visible page from an indexed count-free query before any heavy hydration. Move exact totals, candidate scoring, charts, stale refreshes, and deep page scans to lazy endpoints, explicit queued jobs, or bounded background hydration; never make normal GET preload every future page.
- 2026-05-24 - Performance: Complete parsed page navigation should use keyset/cursor pagination on `(supplier_product_id, pk)`. Count-free pagination with `OFFSET` still slows down on later live pages because the database must skip earlier rows.
- 2026-05-24 - Performance: Complete parsed GET navigation must not synchronously refresh stale parser rows. Show cached queue totals from `NormalizationStatsSnapshot`, keep Next/Previous cursor-based, and use explicit/background refresh jobs for stale parse correctness.
- 2026-05-07 - Operations: visible-row normalization refresh is an explicit operator action and should force-save selected visible parses; global stale refresh should still preserve human-locked rows.
- 2026-05-16 - Performance: normalization queue querysets should avoid duplicate JSON/modifier predicates and keep large parsed-row queue predicates backed by concurrent PostgreSQL indexes; count-free pagination still needs the first visible rows to be cheap to find.
- 2026-05-04 - Performance: Fragrantica/Our Products candidate generation must resolve likely brand IDs first and score only same-brand candidate rows during normal page loads. Avoid full Fragrantica x catalogue perfume scans; use lazy candidate endpoints or cached suggestion state when broader filtering is needed.
- 2026-05-04 - Performance: Catalogue linking workbench rows should reuse server-rendered candidate payloads only when already hydrated. For filtered pages, hydrate the selected row and let non-selected rows fetch fresh payloads on click; this avoids serializing expensive AI/candidate JSON for every visible row while preventing stale list data.
- 2026-05-05 - Performance: Our Products product-list GET requests should attach linked Fragrantica rows only; hidden/unreviewed Fragrantica suggestions belong behind explicit filters, review pages, or lazy candidate endpoints.
- 2026-05-05 - Performance: Catalogue linking strict `100 only` filters should prefilter Fragrantica source rows by likely normalized scent titles before scoring; do not load every staged source for each brand on broad all-brand filter pages.
- 2026-05-05 - Performance: Catalogue linking strict exact filters (`100 only` with suggestions) should use the cheap exact Fragrantica prefilter only as a candidate source, then paginate verified score-100 rows; do not paginate prefilter rows directly or pages will render half empty after real scoring removes conflicts.
- 2026-05-06 - Performance: Catalogue linking high-confidence filters that require scored candidates (`100 only`, `95+`, and `Needs review`) must share the exact prefilter plus verified-pagination path. `Needs review` must include already-linked Fragrantica source rows in the prefilter because manual-review candidates often come from existing primary links or equal-top conflicts.
- 2026-05-06 - Performance: Broad all-brand catalogue linking `95+` and `Needs review` filters must not run global exact-prefilter or verification scans before first render on live data. Hydrate only a bounded page scan for first response; keep exact prefilter/counts for `100 only` and smaller brand/search-scoped high-confidence filters.
- 2026-05-16 - Performance: Broad catalogue linking `Needs review` pages may run a bounded exact-name prefilter inside each scanned batch to avoid candidate scoring for raw catalogue rows that cannot match. Keep it batch-local and count-free; do not turn it into a global prefilter or verification pass.
- 2026-05-06 - Performance: Catalogue linking base querysets must not annotate variant/link counts across the whole catalogue. Use `EXISTS` for linked/unlinked filters, then attach small count summaries only to visible/scored rows.
- 2026-05-07 - Performance: Broad catalogue linking filtered pages should fill the current page from matching rows with bounded forward scans. Do not expose raw scan-window pages; sparse windows leave the Our Products column half empty and make pagination look broken.
- 2026-05-16 - Performance: Catalogue linking filtered pagination must be based on filtered/scored rows, not the raw catalogue page. Fetch one extra filtered match to decide `Next`; never advertise empty filtered pages just because the underlying catalogue queryset has more rows.
- 2026-05-07 - Performance: Broad catalogue linking filtered pages must not pass the exact base queryset count as the candidate scan limit. Keep broad scans bounded like Supplier Products search; use brand/search filters when operators need deeper exact review work.
- 2026-05-07 - Performance: Catalogue linking workbench pagination should avoid Django's exact-count paginator for broad pages. Use count-free `page_size + 1` pagination and show count-free row summaries; keep exact verified pagination only for deliberately exact paths such as `100 only`.
- 2026-05-16 - Performance: Catalogue linking's broad default listing should use cheap deterministic catalogue ordering backed by a PostgreSQL index. Keep brand-name ordering for scoped/search/confidence pages where operator-friendly sorting is worth the narrower query.
- 2026-05-07 - Performance: Broad catalogue linking filtered pages should score generous row batches, not tiny page-sized windows. Keep the broad candidate scan batch at least a few pages/roughly 200 rows so `95+` and `Needs review` filters do fewer repeated Fragrantica candidate query passes while staying bounded.
- 2026-05-07 - Performance: Catalogue/linking/review filter controls should not compute global option counts on every GET. Use plain option labels for broad filters unless a count is already cheap or explicitly needed.
- 2026-05-07 - Performance: Fragrantica product review pages should paginate with fresh `page_size + 1` reads instead of exact catalogue counts. Header copy should show a count-free page/result floor rather than blocking the render on full-table counts.
- 2026-05-08 - Performance: Fragrantica product review status chips should use plain labels, not grouped status counts, on normal GET loads. Avoid `GROUP BY match_status` on broad 124k-row review pages unless an operator explicitly requests exact metrics.
- 2026-05-08 - Performance: Our Products catalogue variant pages should use count-free `page_size + 1` pagination and count-free header copy. Avoid exact `COUNT(*)` totals on the default products tab because the page must stay fresh without cached totals.
- 2026-05-16 - Performance: Our Products concentration audit must not materialize every catalogue perfume and every linked Fragrantica row before pagination. Build audit rows in bounded batches and use count-free page summaries.
- 2026-05-08 - Performance: Shared `prices.view_base.BaseListView` pages should use count-free `page_size + 1` pagination by default and header copy such as "Showing 50+ offers". Do not call `get_queryset().count()` from shared list context just to decorate a page header.
- 2026-05-08 - Performance: Fragrantica linking suggestion lookups depend on `(normalized_brand_name, match_status, normalized_name)`. Keep that lookup indexed with a concurrent PostgreSQL migration because `assistant_linking.FragranticaProduct` is large in production.
- 2026-05-08 - Performance: Fragrantica review/linking search uses `icontains` over staged brand, normalized brand, scent, normalized scent, and collection fields. Keep those high-value text fields covered by PostgreSQL trigram indexes so manual Fragrantica search and linking filters stay fresh without cached/stale result pages.
- 2026-05-08 - Performance: Our Products and catalogue linking search use `icontains` over canonical brand, perfume, collection, concentration, audience, and variant text fields. Keep those catalogue search fields covered by PostgreSQL trigram indexes so broad catalogue searches stay live and fast like Supplier Products search.
- 2026-05-06 - Performance: Our Products delete actions should explicitly bulk-clear known supplier, assistant, and Fragrantica references before deleting catalogue variants/perfumes. Avoid Django's generic cascade collector on live-sized catalogue deletes when the relationship behavior is already known.
- 2026-05-05 - Performance: Supplier Products AJAX search is the reference pattern for fast live search: avoid exact full counts and expensive row hydration, use indexed filters, cheap deterministic ordering, fetch `page_size + 1` rows for pagination, keep only cheap visible-row summaries such as sparklines, and lazy-load heavy details only when the user asks for them.
- 2026-05-05 - Operations: Queue-backed UI actions must verify that a worker is actually registered before reporting a job as queued. If no worker is available, show an operator error and keep the underlying management command directly runnable.
- 2026-05-02 - Operations: heavy Fragrantica staging imports should run on the server with the folder import command, using parsed JSON exports when available to preserve year, audience/gender, and source URLs. Always dry-run first, then run with `--apply` after checking counts.
- 2026-05-02 - Assistant normalization: Unparsed means no saved `ParsedSupplierProduct`; temporary parser previews may help operators inspect visible rows but must not move rows between queues. Use explicit parse jobs/actions to create saved parses, then handle catalogue/link evidence separately.
- 2026-05-02 - Deploy checks: `make local-smoke` is not enough before pushing to `main`. Run `make deploy-gate` for the fast deploy gate; add `DEPLOY_GATE_ARGS="--ui --test ..."` for UI or focused behavior changes, and use `make deploy-gate-full` before high-risk merges or shared parser/linking/schema/import changes.
- 2026-05-03 - Deploy performance: push-to-main CI uses the fast deploy gate by default; full Django tests remain for pull requests and explicit full gates. Production deploy should skip unchanged requirements, static collection, normalization stats, and reparses unless explicitly requested.
- 2026-05-16 - Deploy checks: tests that add rows on top of migration-seeded KB data must assert against a computed baseline/current total, not a hardcoded exact seed count.
- 2026-05-21 - Deploy checks: broad runtime dependency ranges can fail `pip-audit` when CI resolves a newly vulnerable transitive package. Prefer narrow/pinned runtime dependencies after a security-gate failure and verify with `pip-audit --strict -r requirements.txt`.
- 2026-05-03 - Catalogue linking: Fragrantica <-> Our Products review should extend the shared `catalogue_linking_workbench` two-column workflow instead of adding another one-off link button/list page.
- 2026-05-03 - Operations: after all old supplier price folders are prepared, uploaded, and imported, run a final supplier-history reconciliation phase: re-check old emails with current import logic, merge uploaded prepared files with valid email/backfill files, preserve cross-day duplicate price files as separate history, skip only same-day exact duplicates, respect supplier-specific layout/currency rules, validate SKU/article identities, missing dates, duplicate snapshots, and price spikes, and clean loose non-price media only after content/import validation proves the file cannot be a price file.

## Repeated mistakes to avoid

- Do not add one-off parser code for one supplier typo, one collection, or one concentration phrase.
- Do not invent new UI styles when `prices/base.html`, shared components, and CSS utilities already cover the pattern.
- Do not assume SQLite works locally.
- Do not treat staged Fragrantica rows as canonical catalogue products.
- Do not silently overwrite assistant/manual links without explicit staff intent and audit trail.
- Do not leave final summaries without code/docs/tests/follow-up fields.
- Do not claim a visual UI change is fixed until the CSS rule that controls the actually visible scroll/work area has been updated and checked against the user's viewport.
- Do not treat failed deploys, failed checks, parser misses, bad link suggestions, or UI regressions as isolated incidents; extract the reusable cause and add a rule, KB seed, test, checker, or doc note when it can prevent the same error later.

## Lessons learned

- Existing docs are useful but large; new agent docs should summarize and link instead of copying them.
- `assistant_linking/docs/assistant_learning_design.md` is the source of truth for assistant learning discipline.
- The canonical catalogue lives in `catalog`; supplier offers and import history live in `prices`.
- Shared UI components already exist for page headers, tabs, pagination, and table empty states.
- 2026-05-04 - Area: catalogue linking UI height. Source: repeated user correction after the workbench columns stayed visually cramped.
- 2026-05-05 - Area: UI iteration feedback. Source: user correction. Treat direct feedback like "very good", "very bad", "perfect", and screenshot comparisons as reusable design signal for the next iteration, not just approval/disapproval of one patch.

## Future note format

Add future notes under the appropriate section using this exact format:

```text
- YYYY-MM-DD - Area: concise lesson/risk/priority. Source: task, bug, or user correction.
```

Only add notes that should affect future work.
