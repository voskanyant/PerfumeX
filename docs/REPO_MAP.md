# Repository Map

## Purpose of this document

Current architecture, app ownership, and file-placement map. Use this before adding files, routes, services, models, templates, static assets, tests, or commands.

Related docs: [AGENTS.md](../AGENTS.md), [README.md](../README.md), [docs/DOMAIN_MODEL.md](DOMAIN_MODEL.md), [docs/UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md), [docs/DECISIONS.md](DECISIONS.md), [docs/WORKING_RULES.md](WORKING_RULES.md), [docs/CODEX_TASKS.md](CODEX_TASKS.md), [docs/DRIFT_CHECKLIST.md](DRIFT_CHECKLIST.md), [PROJECT_HANDOFF.md](../PROJECT_HANDOFF.md).

## Current Architecture

PerfumeX is a server-rendered Django 5 project with PostgreSQL-only settings.

- `perfumex/settings.py` owns installed apps, middleware, static/media config, OpenAI model settings, logging, and PostgreSQL enforcement.
- `perfumex/urls.py` routes public viewer pages at `/`, `/import-prices/`, and `/products/...`; custom staff routes under `/admin/`; Django's stock admin under `/django-admin/`.
- Installed local apps are `catalog`, `assistant_core`, `assistant_linking`, then `prices`.
- `prices.middleware.AdminPanelStaffOnlyMiddleware` protects `/admin/` while allowing limited non-staff import/status routes.
- Templates are discovered through app template directories (`TEMPLATES["DIRS"]` is empty, `APP_DIRS=True`).
- Shared UI shell lives in `prices/templates/prices/base.html`; shared UI include entry points live in `prices/templates/includes/`.
- Static assets are app-local: most CSS/JS under `prices/static/prices/`, linking queue shortcuts under `assistant_linking/static/assistant_linking/`.

## App Map

### `prices/`

Primary operational app for suppliers, imports, prices, viewer pages, and the older internal product workflow.

Owns:
- Supplier, mailbox, mailbox cursor, mailbox rule, supplier price source, and supplier file mapping models.
- Supplier product rows, import batches/files, price snapshots, stock snapshots, exchange rates, import settings, user preferences.
- Legacy/internal `OurProduct` records and older direct product linking screens.
- Shared Our Products <-> Fragrantica catalogue linking workbench, including bidirectional review links and bulk reviewed link actions.
- Public viewer product list/detail/search routes.
- Custom `/admin/` dashboard, supplier overview, import board, import logs, import settings, currency, user/group, supplier product, and `OurProduct` screens.
- File/email/import services in `prices/services/`: importer, email importer, link downloader, CBR rates, background runner, RQ job dispatch, supplier-board status, product filters, product visibility.
- Import/email/media cleanup, repair, CBR sync, and RQ worker management commands.
- Shared templates, base layout, auth template, reusable `includes/*` partials, and most shared CSS/JS.

Should not own:
- New canonical perfume facts or source claims; put those in `catalog`.
- New assistant parsing, alias, normalization, grouping, or suggestion rules; put those in `assistant_linking`.
- New assistant research, knowledge, catalogue CRUD, or OpenAI orchestration; put those in `assistant_core`.
- One-off business fixes hardcoded in views/templates when they belong in mappings, rules, aliases, or services.

### `catalog/`

Canonical perfume catalogue data app. It intentionally has models/tests/admin but no current views/forms/templates.

Owns:
- `Brand`, brand-scoped `Collection`, `Perfume`, `PerfumeVariant`, `Note`, `Accord`, `Source`.
- Perfume note/accord through models.
- `FactClaim` and `AIDraft` records used by assistant research and review.
- Model behavior and catalogue integrity tests.

Should not own:
- Supplier imports, email state, price files, or price snapshots.
- Supplier-specific raw text parsing or alias rule UI.
- Staff UI screens unless the project intentionally creates a catalogue app UI in a future refactor.

### `assistant_core/`

Staff assistant shell for knowledge management, catalogue maintenance UI, research jobs, claims, and drafts.

Owns:
- Assistant dashboard and staff-only assistant routes under `/admin/assistant/`.
- Global/supplier rules, knowledge notes, brand watch profiles, source snapshots, detected changes.
- Catalogue CRUD/import/cleanup screens for `catalog` models.
- Research job, claim, draft, and perfume research review screens.
- Forms for rules, knowledge, brand watch, catalogue CRUD/merge/import.
- Services for catalogue import parsing, catalogue import POST actions, catalogue query/context helpers, catalogue cleanup/merge actions, assistant dashboard/context building, knowledge and alias section/query context, knowledge POST actions, review/research action wrappers, mock/OpenAI brand research, mock/OpenAI draft writing, and OpenAI Responses calls.
- Public assistant-core view exports in `assistant_core/views.py`, with focused view modules for dashboard, knowledge/aliases/rules, catalogue management, research/brand-watch, and shared staff mixins.
- Assistant UI templates under `assistant_core/templates/assistant_core/`.

Should not own:
- Low-level supplier product parser implementation.
- Match grouping, normalization queues, link decisions, or undoable linking operations.
- Price import/email processing.
- Automatic publishing of assistant-generated facts without review state.

### `assistant_linking/`

Staff assistant app for deterministic supplier-product normalization and linking.

Owns:
- Brand/product/concentration aliases.
- Staged external catalogue rows (`FragranticaProduct`).
- Parsed supplier product rows and normalization stats snapshots.
- Match groups/items, manual link decisions/audits, link actions, and link suggestions.
- Normalization queues, parsed product detail/teaching flow, group queue/detail, and product workbench UI.
- Parser, parser-rules, garbage keyword, catalogue matcher, grouping, group rebuild/action helpers, group queue/detail queries, smart search, HTML catalogue importer, normalization dashboard/list/detail context and visible-list refresh helpers, product workbench context, alias form, teaching-flow, bulk link target selection/response/status/undo, and mutation action helpers, manual link decision recording, stats, and suggestion action/generation services.
- Linking-specific management commands: reparse supplier products, rebuild groups, refresh stats, import brand catalogue HTML.
- Deep app docs in `assistant_linking/docs/`.

Should not own:
- Supplier file/email import mechanics.
- Canonical catalogue model definitions.
- Price snapshot or stock history logic.
- Hardcoded brand/product corrections that should be data-backed aliases, parser rules, catalogue facts, or knowledge notes.

## Where New Code Should Go

- Root URL/settings/middleware behavior: `perfumex/`, or `prices/middleware.py` for existing custom admin access rules.
- Shared generic CRUD view base classes and staff/permission mixins: `prices/view_base.py`.
- Generic admin CRUD views for import batches/files, snapshots, and exchange-rate records: `prices/views_records.py`.
- Account/profile plus user and group management views: `prices/views_accounts.py`.
- Basic supplier CRUD/detail views: `prices/views_suppliers.py`; supplier import board stays with import/board UI until split further.
- Supplier overview/import board entry point for staff and public import status: `prices/views_supplier_overview.py`.
- Mailbox, supplier mailbox rule, and supplier file mapping configuration views: `prices/views_import_config.py`.
- Import detailed logs, stuck email-run recovery, import scheduler/settings, import wizard/detail/delete, supplier import page, quick upload, supplier price-source, supplier email action/status, mapping preview, supplier rate recalculation, and bulk price reimport views: `prices/views_imports.py`.
- Currency/exchange-rate settings views: `prices/views_currency.py`.
- Dashboard and documentation shell views: `prices/views_shell.py`.
- Supplier-product list/search/detail, viewer product list/search/detail, cleanup, bulk delete, link, and generic CRUD views: `prices/views_supplier_products.py`.
- Legacy `OurProduct` CRUD/detail plus catalogue variant management/review views: `prices/views_our_products.py`.
- Supplier/import/email/price-source business logic: `prices/services/`.
- Import wizard initial/upload orchestration, supplier import page context/initial/url helpers, supplier import form action orchestration, supplier price upload form and quick-upload action results, import-batch single/bulk delete and import-board redirect orchestration, active mapping lookup, supplier import mapping form helpers/action results, mapping preview request/result handling, price-source create/delete/import orchestration/status/action results, and bulk price reimport queue dispatch/action results: `prices/services/import_operations.py`.
- Import history timestamp, processed price import-batch query, import-date collection helpers, and import detail display context: `prices/services/import_history.py`.
- Import detailed-log queryset construction, run/batch/diagnostic filtering, date parsing, console log rendering, and page context assembly: `prices/services/import_logs.py`.
- Email import run timeout/stale-running checks, status payload construction, stuck-run listing/context/recovery action results, user-cancel action results, manual, supplier email-import, supplier backfill, bulk backfill, and forced mailbox-scan action results, import/backfill date-range parsing, active supplier selection for email backfills, run creation/message helpers, bulk backfill run creation, forced mailbox scan dispatch, process-email command argument/queue dispatch helpers, and dispatch-failure status updates: `prices/services/email_import_runs.py`.
- Background job dispatch: `prices/services/job_queue.py`; worker process command: `prices/management/commands/run_rq_worker.py`.
- Queue-backed commands should remain directly callable and be dispatchable through the job queue where practical.
- Production service templates for queue workers and similar deployment helpers: `deploy/systemd/`.
- Supplier import board/status helper logic, summary rows, and full email status endpoint payload assembly: `prices/services/supplier_board.py`.
- Automatic mailbox scan/import-board status summaries: `prices/services/autoimport_status.py`.
- Cron runner script, crontab install/remove action results, next-run calculation, scheduler status logic, import settings page context, and import settings POST action orchestration: `prices/services/import_scheduler.py`.
- Exchange-rate lookup, CBR markup-rate sync summaries/recalculation, supplier rate-recalculation action results, price conversion, display formatting, and price deltas: `prices/services/currency.py`.
- Legacy catalogue variant list/search context, catalogue tab action/message/redirect handling, inline variant action/redirect/mutation handling, `OurProduct` detail context/offer filtering, Fragrantica review page context/evidence grouping, and catalogue review/search helpers for `OurProduct` and Fragrantica comparison screens: `prices/services/catalog_review.py`.
- Catalogue display/formatting helpers for reviewed catalogue names, collection title casing, accent folding, and display-safe perfume names: `prices/services/catalog_formatting.py`. Keep compatibility imports in `catalog_review.py` while callers migrate.
- Supplier product base queryset, list/search filter pipeline, viewer saved-filter redirects, sorting, ordering-plan policy, and filter template context helpers: `prices/services/product_filters.py`.
- Supplier product list/detail display helpers such as list/detail context handling, detail back-url handling, sparklines, AJAX search payload and row/page serialization, price-history query filtering, and pagination/chart preparation: `prices/services/product_display.py`.
- Supplier product cleanup action/redirect handling, bulk delete action/redirect handling, and link-form construction/mutation helpers: `prices/services/product_operations.py`.
- Older direct product-linking source lookup, list context/query policy, search request/payload orchestration, apply action parsing/mutation dispatch, candidate search query policy/response serialization, text normalization, candidate scoring, and legacy `OurProduct` link mutation helpers: `prices/services/product_linking.py`.
- Supplier/import/email/price-source UI: focused `prices/views_*.py` modules re-exported by `prices/views.py`, `prices/forms.py`, `prices/urls.py`, `prices/templates/prices/`.
- Basic supplier CRUD/detail UI: `prices/views_suppliers.py`.
- Import configuration UI for mailboxes, mailbox rules, and file mappings: `prices/views_import_config.py`.
- Import logs, stuck-run recovery, import scheduler settings, import wizard/detail/delete, supplier import page, quick upload, supplier price-source, supplier email action/status, mapping preview, supplier rate recalculation, and bulk price reimport UI: `prices/views_imports.py`.
- Currency/exchange-rate settings UI: `prices/views_currency.py`.
- Dashboard and documentation shell UI: `prices/views_shell.py`.
- Supplier-product staff/viewer list, search, detail, mutation, and generic CRUD UI: `prices/views_supplier_products.py`.
- Older direct product-linking page, AJAX candidate search, and apply action: `prices/views_product_linking.py`.
- Legacy `OurProduct` UI and catalogue variant management/review screens: `prices/views_our_products.py`; check whether new product-identity features belong in `catalog` or assistant apps first.
- User/profile/group settings UI: `prices/views_accounts.py`, `prices/forms.py`, and shared templates.
- Public viewer product UI: `prices/views_supplier_products.py`, public routes in `perfumex/urls.py`, templates in `prices/templates/prices/`.
- Internal product (`OurProduct`) UI: current home is `prices/views_our_products.py`, but prefer checking whether the feature really belongs to `catalog` first.
- Canonical catalogue schema/model behavior: `catalog/models.py` and `catalog/tests/`.
- Catalogue management UI and imports: `assistant_core/views.py`, `assistant_core/forms.py`, `assistant_core/services/catalog_importer.py`, templates under `assistant_core/templates/assistant_core/catalog/`.
- Assistant dashboard UI: `assistant_core/views_dashboard.py`.
- Assistant knowledge, aliases, and rules UI: `assistant_core/views_knowledge.py`.
- Catalogue management UI: `assistant_core/views_catalog.py`.
- Assistant research, brand-watch, claims, and drafts UI: `assistant_core/views_research.py`.
- Public assistant-core view exports: `assistant_core/views.py`; shared staff view mixins: `assistant_core/view_mixins.py`.
- Assistant knowledge/rules/research/review/OpenAI behavior: `assistant_core/services/`, focused `assistant_core/views_*.py` modules, and `assistant_core/forms.py`.
- Parser, alias, normalization, grouping, smart-search, and linking behavior: `assistant_linking/services/`.
- Review-only AI advice and learning proposals for normalization and Fragrantica linking: `assistant_linking/services/`, storing auditable recommendation/proposal rows in `assistant_linking` while reusing the shared OpenAI wrapper in `assistant_core`.
- Public assistant-linking view exports: `assistant_linking/views.py`; normalization UI: `assistant_linking/views_normalization.py`; group/linking/workbench UI: `assistant_linking/views_linking.py`; shared assistant staff view mixins: `assistant_linking/view_mixins.py`; forms in `assistant_linking/forms.py`; templates under `assistant_linking/templates/assistant_linking/`.
- Management commands: the owning app's `management/commands/`.
- Template tags: owning app's `templatetags/`; currently shared price display helpers are in `prices/templatetags/prices_extras.py`.
- Shared UI partials: `prices/templates/includes/`; implementation details may remain in `prices/templates/prices/components/`.
- Shared CSS/JS: `prices/static/prices/`; linking-only keyboard shortcuts stay under `assistant_linking/static/assistant_linking/`.
- Tests: the owning app's tests package/file. Add narrow tests near the behavior changed.
- Durable agent docs: `docs/`; deep linking-assistant operating docs: `assistant_linking/docs/`.
- Project-managed Codex skills: `codex-skills/`, linked into `%USERPROFILE%\.codex\skills` by `scripts/sync_codex_skills.ps1`.

## Where Not To Put New Code

- Do not add reusable business logic to templates.
- Do not add reusable import/parsing/linking logic as private helpers in views if a service already owns that area.
- Do not grow top-level helper clusters in focused `views_*.py` modules. Existing helper functions are allowlisted by `scripts/check_view_exports.py`; reusable helpers belong in the owning `services/` module.
- Do not add canonical perfume fields or fact semantics to `prices` models.
- Do not add supplier import/email code to `assistant_core` or `assistant_linking`.
- Do not put new shared templates in a root `templates/` directory unless settings are intentionally changed.
- Do not treat `media/`, `logs/`, `tmp/`, `.ruff_cache/`, or `staticfiles/` as source locations.
- Do not copy old scratch work from `tmp/importer-no-filename-worktree/` unless a task explicitly asks and the code is reviewed.

## Templates And Static Files

- Main templates extend `prices/base.html`.
- New shared includes should use `prices/templates/includes/` paths such as `includes/page_header.html`, `includes/tabs.html`, `includes/pagination.html`, `includes/table_empty.html`, `includes/empty_state.html`, and `includes/messages.html`.
- Use `docs/UI_DESIGN_SYSTEM.md` before changing templates, CSS, or UI JavaScript.
- `prices/static/prices/css/app.css` is shared shell/system CSS.
- `pages.css`, `products.css`, `imports.css`, and `detail.css` are narrower page/domain CSS files.
- `prices/static/prices/js/app.js` owns shared drawer, flash, submit, confirm, and form/table decoration behavior.
- Product list/detail/linking/import behavior has dedicated JS files; do not fork them without documenting a reusable hook.

## Tests

- `prices/tests.py` covers shared UI components, frontend hardening, mailbox security, link importer, email cursors, permissions, background safety, `OurProduct`, import boundaries, media hygiene, diagnostics, scheduler, and hidden product keywords.
- `catalog/tests/` covers catalogue model behavior.
- `assistant_core/tests/` covers dashboard, knowledge/research, catalogue management, claims/drafts/OpenAI safety.
- `assistant_linking/tests/` covers normalizer, smart search, teaching parse, HTML catalogue import, grouping/workbench, and concurrent suggestion acceptance.
- Prefer targeted tests in the owning app; use cross-app tests only when the behavior truly crosses app boundaries.

## Known Messy Areas

- `prices/views.py` is now a compatibility export module for focused `prices/views_*.py` modules; do not add new workflow code there.
- `prices/views.py`, `assistant_core/views.py`, and `assistant_linking/views.py` are import-only compatibility modules guarded by `scripts/check_view_exports.py`; they should import only focused `views_*.py` modules, except for approved shared view mixins, and real view logic belongs in focused modules.
- `prices` still owns `OurProduct` and older linking screens while `catalog` owns canonical `Brand`/`Perfume`/`PerfumeVariant`.
- `SupplierProduct` can link to `OurProduct`, `catalog.Perfume`, and `catalog.PerfumeVariant`; do not assume these are interchangeable.
- Assistant alias models live in `assistant_linking`, but their CRUD is exposed partly through `assistant_core` knowledge screens.
- Both `assistant_core` and `assistant_linking` are mounted under `/admin/assistant/`; route namespacing matters.
- Some pagination/tab/table markup predates shared `includes/*` partials; migrate only when touching the screen and the replacement is obvious.
- `db.sqlite3` may exist locally, but runtime settings reject non-PostgreSQL engines.
- `tmp/` contains historical scratch/worktree material, not active source.

## Future Refactors

These are future options, not work to do during documentation or narrow feature tasks.

- Future: continue reducing `prices/views.py` toward compatibility exports only.
- Future: keep `prices/views.py` as view exports only; import reusable helpers from their owning `prices/services/*` modules.
- Future: clarify the long-term boundary between legacy `OurProduct` and canonical `catalog` models.
- Future: consider moving alias CRUD fully into `assistant_linking` or clearly documenting why `assistant_core` owns the screens.
- Future: continue migrating safe pagination/tabs/empty-state usages to `prices/templates/includes/`.
- Future: consider app-specific URL prefixes if `/admin/assistant/` route overlap becomes confusing.
