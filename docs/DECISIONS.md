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
