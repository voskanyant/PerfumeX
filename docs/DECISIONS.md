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
