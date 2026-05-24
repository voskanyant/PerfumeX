# Decisions

## Purpose of this document

Decision log for durable architectural, business, product, and design choices. Use this when a change creates or depends on a decision future agents must preserve.

Related docs: [AGENTS.md](../AGENTS.md), [README.md](../README.md), [docs/REPO_MAP.md](REPO_MAP.md), [docs/DOMAIN_MODEL.md](DOMAIN_MODEL.md), [docs/UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md), [docs/WORKING_RULES.md](WORKING_RULES.md), [docs/CODEX_TASKS.md](CODEX_TASKS.md), [docs/DRIFT_CHECKLIST.md](DRIFT_CHECKLIST.md).

## Format

Use this format for future entries:

```text
## YYYY-MM-DD - Title

Status: Proposed | Accepted | Superseded
Context: What prompted the decision.
Decision: What we decided.
Consequences: What future agents should preserve or watch.
```

Meaningful architectural, business, or design decisions from chat must be added here when they should guide future work.

## 2026-04-30 - README remains human entry point

Status: Accepted

Context: The repository needs self-documentation for Codex/AI agents without displacing existing human documentation.

Decision: Keep `README.md` as the main human entry point and use `AGENTS.md` as the main AI-agent entry point. Keep focused agent docs in `docs/`.

Consequences: Future agent-oriented rules should go in `AGENTS.md` or `docs/`; README should stay setup/product oriented.

## 2026-04-30 - App ownership follows current Django boundaries

Status: Accepted

Context: The codebase now has `prices`, `catalog`, `assistant_core`, and `assistant_linking`, while older docs still describe `prices` as the single main app.

Decision: Treat `prices` as owner of supplier/import/price/viewer operations, `catalog` as owner of canonical perfume facts, `assistant_core` as owner of assistant shell/knowledge/research/catalog admin, and `assistant_linking` as owner of parsing/aliases/normalization/matching/link decisions.

Consequences: New code should be placed according to those boundaries unless a deliberate architecture change is made and logged.

## 2026-04-30 - Assistant learning uses knowledge before code

Status: Accepted

Context: Existing assistant docs and migrations show many parser/alias corrections and warn against one-off parser fixes.

Decision: For incorrect normalization, prefer catalogue facts, aliases, concentration aliases, and editable rules before changing parser code. Parser code changes are for reusable capabilities.

Consequences: Future parser tasks should read `assistant_linking/docs/assistant_learning_design.md` and update data/rules where possible.

## 2026-04-30 - Staged external catalogue rows are evidence

Status: Accepted

Context: `assistant_linking.FragranticaProduct` and HTML import docs stage saved external catalogue pages for review.

Decision: External catalogue imports stage evidence only. They must not directly create canonical catalogue products or aliases without review.

Consequences: Preserve dry-run/review behavior for external catalogue import workflows.

## 2026-04-30 - Reuse existing UI system

Status: Accepted

Context: Templates already share `prices/base.html`, shared components, and a consistent CSS vocabulary.

Decision: New UI work should reuse existing layout, button, table, tabs, pagination, empty-state, flash, form, and drawer patterns.

Consequences: UI tasks must read `docs/UI_DESIGN_SYSTEM.md` before editing templates/static files.

## 2026-04-30 - Background jobs use RQ and Redis

Status: Accepted

Context: Import, email, CBR, and normalization work can outlive a web request and should not rely on web-process threads or ad hoc subprocess kickoff long term.

Decision: Use RQ with Redis as the background job foundation. Keep management commands directly callable, and dispatch queue-safe work through `prices.services.job_queue`.

Consequences: Production needs Redis and an RQ worker process. Future background work should be added to the queue layer instead of spawning web threads.

## 2026-05-02 - Assistant queues avoid implicit parse work on GET

Status: Accepted

Context: Live-sized restored data made the unparsed normalization queue slow because opening the page reparsed visible rows before rendering.

Decision: Normal assistant queue GET requests should list existing rows only. Expensive parse/reparse refreshes must be explicit operator actions.

Consequences: Keep list pages fast against live data; move broader refresh work to explicit buttons, commands, or background jobs.

## 2026-05-02 - Fragrantica updates catalogue only through reviewed links

Status: Accepted

Context: Fragrantica should guide brand and fragrance identity, while Our Products stores local concentration and sellable variant data used by supplier normalization.

Decision: Treat staged Fragrantica rows as external source truth for brand/name/collection/audience/year only after staff links a row to a local `catalog.Perfume`. Preserve local concentration and variants during that apply step.

Consequences: Supplier normalization should continue reading local catalogue data. Fragrantica import/review work should create or update local catalogue facts through auditable link/apply actions, not by silently replacing catalogue rows.

## 2026-05-02 - Promote reviewed catalogue data separately from code

Status: Accepted

Context: Local Fragrantica/catalogue edits live in PostgreSQL and do not move to production through Git deploys.

Decision: Deploy code through Git, then promote reviewed Fragrantica catalogue links with explicit export/import JSON commands. The import updates staged Fragrantica rows and reviewed catalogue identity only; it must not replace live supplier products, import history, prices, or snapshots.

Consequences: Do not copy a full local database to live for catalogue edits. Use dry-run import on live before applying reviewed catalogue bundles.

## 2026-05-02 - Production static assets use hashed filenames

Status: Accepted

Context: Browser-cached CSS made deployed pages render with old layout rules even after `collectstatic` copied the updated files.

Decision: Use WhiteNoise compressed manifest static storage so changed CSS/JS files receive content-hashed URLs after deploy.

Consequences: Keep `collectstatic` in the deploy workflow. Do not switch production static files back to stable unhashed filenames unless another cache-busting mechanism replaces it.

## 2026-05-02 - Manual mailbox scan falls back when Redis is unavailable

Status: Accepted

Context: Production Redis downtime prevented manual mailbox scans from starting through RQ, even though the cron runner can execute `import_emails` directly.

Decision: Manual "run now" mailbox scans should try RQ first, then run `import_emails --force` synchronously if queue dispatch fails.

Consequences: Operators can still trigger a scan during a queue outage. Redis and an RQ worker remain required for normal background job throughput.

## 2026-05-02 - Catalogue collections are brand-scoped

Status: Accepted

Context: Fragrantica catalogue imports include collection/line names, and the same collection name can appear under different brands.

Decision: Store collections as `catalog.Collection` rows scoped to one `catalog.Brand`. Keep existing text fields during the transition, but resolve catalogue perfumes and Fragrantica staging rows to the brand-scoped collection relationship.

Consequences: Matching and import code must compare collections within a brand. Do not treat collection name alone as globally unique.

## 2026-05-02 - Saved parses define normalization queue state

Status: Accepted

Context: Unparsed rows can appear well normalized when the parser preview is good, but they remain unparsed until a `ParsedSupplierProduct` is saved.

Decision: Treat `SupplierProduct.assistant_parse` as the queue boundary. A row leaves Unparsed only when an explicit parse job/action saves a `ParsedSupplierProduct`; visible-page previews are allowed only as non-persistent display.

Consequences: Add bulk parse actions or background jobs for backlog processing. Do not let detail-page GETs or preview rendering silently move rows between queues. Keep matching/link evidence as a separate review layer from basic parse completeness.

## 2026-05-03 - Catalogue linking uses one shared workbench

Status: Accepted

Context: Staff need to link Fragrantica rows from either Fragrantica Products or Our Products without bouncing between separate list/detail pages.

Decision: Use the shared `/admin/our-products/linking/` two-column workbench for bidirectional Our Products <-> Fragrantica linking, confidence filtering, live candidate lookup, and bulk reviewed link actions.

Consequences: Future Fragrantica/Our Products linking improvements should extend this workbench and its matching services instead of adding parallel link controls with separate behavior.

## 2026-05-03 - Deploy pipeline skips repeated work

Status: Accepted

Context: Main-branch deploys were spending most time in serial Django tests, then repeating production setup work even when requirements, static assets, or normalization inputs did not change.

Decision: Run CI Django tests in parallel and keep deploy steps change-aware. Production deploys should install requirements, collect static files, refresh normalization stats, or run parser reparses only when inputs changed or an explicit deploy environment option requests it.

Consequences: Do not reintroduce fixed per-deploy reparses or unconditional package/static work. Use explicit maintenance commands or `DEPLOY_REPARSE_TERMS` for targeted production parser refreshes.

## 2026-05-03 - Deploy gate has fast and full modes

Status: Accepted

Context: Focused corrections were paying the cost of full test and repository check runs before every deploy.

Decision: `make deploy-gate` runs the fast deploy gate by default: Django check, migration generation check, migration plan, and the Makefile target-surface guard. Add `DEPLOY_GATE_ARGS="--ui --test ..."` for UI syntax checks and targeted tests. Use `make deploy-gate-full` before big merges, schema changes, parser/linking logic changes, import/deletion behavior, shared service refactors, or batched releases.

Consequences: Do not require full Django tests for every small deploy. Pick the gate based on blast radius, and document any skipped high-risk checks in the task summary.

## 2026-05-04 - Fragrantica links stay primary-first

Status: Accepted

Context: Some local Our Products perfumes may rarely represent the same external Fragrantica product, but broad many-to-many linking would make normal matching and bulk actions less safe.

Decision: Keep `FragranticaProduct.matched_perfume` as the primary reviewed link and store rare second reviewed links in `assistant_linking.FragranticaProductLink`. The workbench may expose these only as manual-review candidates, never as bulk-ready numeric-confidence matches.

Consequences: Preserve one Fragrantica row to one Our Products perfume as the default. Promotion export/import must carry both primary and manual-extra links.

## 2026-05-05 - AI assistant is review-only and bounded

Status: Accepted

Context: AI can help with normalization and Fragrantica linking, but automatic AI mutations would make parser behavior, links, aliases, and catalogue facts hard to audit.

Decision: AI outputs for normalization and linking must be stored as reviewed recommendations first. Deterministic services still generate candidate sets and business rules; AI receives only bounded context, must return strict JSON referencing existing IDs, and must not create links, aliases, parser rules, or catalogue facts by itself.

Consequences: Future AI UI should expose pending recommendations with reasoning and risk. Accepted advice may create a pending learning proposal, but links, aliases, parser rules, and catalogue facts still have to be applied through the same audited mutation paths already used by deterministic workflows. A proposal apply button is a staff action, not AI auto-publishing.

## 2026-05-16 - Complete parsed page refreshes visible stale parses

Status: Superseded by 2026-05-24 keyset pagination and cached totals decision

Context: Operators saw stale saved parse identities on the Complete parsed products page, then opening the product detail refreshed the row and made the list correct after returning.

Decision: The Complete parsed products page may refresh only the visible unlocked saved parses whose `parser_version` is stale during normal GET rendering. Other normalization queues keep explicit refresh behavior, and human-locked parses are preserved.

Consequences: This improved display correctness but made later page navigation do parser writes during GET, so it is no longer the normal list behavior.

## 2026-05-19 - Complete parsed queue uses stored membership

Status: Accepted

Context: Production-sized Complete parsed pages were still too slow when each GET recalculated complete-parse predicates across saved parse rows.

Decision: Store Complete parsed queue membership on `ParsedSupplierProduct.is_complete_parse`, maintain it on parse saves/edits, and query the list page through an indexed flag.

Consequences: Do not rebuild this queue with broad JSON/predicate scans during normal page loads. Backfill or refresh the flag whenever future changes alter what "complete parsed" means.

## 2026-05-19 - Large list pages render first, then hydrate

Status: Accepted

Context: Live operator pages with tens or hundreds of thousands of rows, such as Complete parsed, catalogue linking, and Supplier Products, must stay usable even when exact counts, candidate matching, charts, or later pages are expensive.

Decision: Large list pages must return the first visible page through cheap indexed, count-free queries. Heavy work belongs behind lazy endpoints, explicit queued actions, or bounded background hydration after the first response; do not synchronously scan all matching rows, compute exact totals, or preload every future page on normal GET.

Consequences: Treat Supplier Products AJAX search as the reference pattern. Background loading should hydrate details, suggestions, exact metrics, or nearby pages selectively; it must not start an unbounded "open every page" crawl that increases database pressure.

## 2026-05-24 - Complete parsed uses cursor paging and cached totals

Status: Accepted

Context: Complete parsed first-page loading became fast, but changing pages still became slow when normal GETs mixed cursor navigation with synchronous stale parser refreshes. Operators also need visible product/page totals without restoring expensive count queries.

Decision: Complete parsed navigation uses keyset/cursor links on `(supplier_product_id, pk)`, displays cached totals from `NormalizationStatsSnapshot`, and does not refresh stale parser rows synchronously during normal GET rendering. Stale correctness work belongs to explicit visible-page refresh actions, queued stale refresh jobs, or future bounded async hydration.

Consequences: Page navigation stays fast and page/product totals stay visible. Counts are only as fresh as the stats snapshot; refresh stats after large parse/link/import jobs when exact dashboard/list totals matter.
