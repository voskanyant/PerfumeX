# AGENTS.md

## Purpose of this document

Primary entry point for Codex and other AI agents working in PerfumeX. Start here before code, UI, data, architecture, or documentation changes.

## Documentation hierarchy

- Human entry point: [README.md](README.md).
- AI-agent entry point: [AGENTS.md](AGENTS.md).
- Current architecture and file ownership: [docs/REPO_MAP.md](docs/REPO_MAP.md).
- Business/domain terms and distinctions: [docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md).
- UI/template/static rules: [docs/UI_DESIGN_SYSTEM.md](docs/UI_DESIGN_SYSTEM.md).
- Active agent priorities, risks, and lessons: [docs/CODEX_TASKS.md](docs/CODEX_TASKS.md).
- Durable decisions: [docs/DECISIONS.md](docs/DECISIONS.md).
- Safe-change workflow: [docs/WORKING_RULES.md](docs/WORKING_RULES.md).
- Final review checklist: [docs/DRIFT_CHECKLIST.md](docs/DRIFT_CHECKLIST.md).
- Maintainer/operator deep reference: [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md).
- Historical audit prompt reference: [AUDIT_AND_CODEX_PROMPTS.md](AUDIT_AND_CODEX_PROMPTS.md).
- Historical alias/parser prompt reference: [KB_ALIASES_AND_CODEX_PROMPTS.md](KB_ALIASES_AND_CODEX_PROMPTS.md).
- Contribution checks: [CONTRIBUTING.md](CONTRIBUTING.md).

`README.md` and `AGENTS.md` are primary entry points. The focused `docs/*.md` files are current repo memory. Old audit, prompt, and handoff docs are specialized references; verify them against current code before acting on them.

PerfumeX is a Django 5 application for supplier price ingestion, price history, supplier-product normalization, internal catalogue linking, and staff review workflows. The human entry point remains `README.md`; this file is the agent entry point.

## Project overview

PerfumeX imports supplier price files manually or from email, stores current supplier offers and price/stock history, normalizes supplier product names against catalogue knowledge, links supplier rows to internal/canonical products, and gives staff review queues for assistant-driven catalogue and linking work. The app is server-rendered Django, PostgreSQL-only, and uses the custom `/admin/` workspace separately from `/django-admin/`.

## Default task protocol

For every task:
1. Read this file first.
2. For any non-trivial change, read `docs/REPO_MAP.md`, `docs/DOMAIN_MODEL.md`, and `docs/CODEX_TASKS.md`.
3. If UI/templates/static files are touched, read `docs/UI_DESIGN_SYSTEM.md` and use the project `ui-ux-pro-max` skill for UI/UX judgment. If the skill is not loaded in the current Codex session, read `codex-skills/ui-ux-pro-max/SKILL.md` directly before making UI decisions.
4. If business behavior, architecture, file placement, or app ownership is touched, read `docs/DECISIONS.md`.
5. Reuse existing patterns before creating new ones.
6. Do not invent a new design if an existing pattern exists.
7. When the user's requested implementation can be improved with a better approach, stronger layout, safer workflow, or useful adjacent function, briefly propose it and explain why it is better before or during implementation.
8. After any change, check whether docs should be updated.
9. Update docs only if a durable new rule, pattern, decision, or lesson was discovered.
10. The repo memory lives in files, not in chat. Future prompts should usually be short: "Read AGENTS.md first. [task]"
11. Every task summary must say:
   - Code changed
   - Docs changed
   - Tests/checks run
   - Follow-up notes

For trivial read-only questions, use judgment; for code, UI, data, architecture, or workflow changes, follow the non-trivial checklist above.

## Required reading for non-trivial changes

Before any non-trivial change, inspect:
- `AGENTS.md`
- `docs/REPO_MAP.md`
- `docs/DOMAIN_MODEL.md`
- `docs/CODEX_TASKS.md`
- `docs/UI_DESIGN_SYSTEM.md` and the project `ui-ux-pro-max` skill if UI/templates/static files are touched
- `docs/DECISIONS.md` if architecture or business behavior is touched

Also read app-local docs when the task enters their area, especially `assistant_linking/docs/assistant_learning_design.md` for assistant learning, aliases, parsing, catalogue imports, and linking workflows.

## After-change doc check

After any change, ask whether it created durable repo knowledge. Update docs when it did; leave docs alone when it did not.

Use this destination map:
- UI/design correction or pattern: `docs/UI_DESIGN_SYSTEM.md`
- business/domain meaning: `docs/DOMAIN_MODEL.md`
- architecture or durable business/design decision: `docs/DECISIONS.md`
- file placement or app ownership: `docs/REPO_MAP.md`
- workflow or repeated Codex behavior: `AGENTS.md` or `docs/WORKING_RULES.md`
- current task memory, active warnings, repeated mistakes, or lessons: `docs/CODEX_TASKS.md`

Keep docs short:
- Do not dump long chat transcripts.
- Add short durable rules.
- Prefer examples over long explanations.

If the user corrects the same type of mistake twice, add a permanent rule to the relevant doc.

## Self-documenting memory protocol

The repo memory lives in files, not in chat.

When the user says "remember", "always", "never", "from now on", "same as before", or corrects a repeated mistake, consider whether a durable project rule should be added to the relevant doc. If the user explicitly asks to make it permanent, update the relevant doc in the same task.

Store durable project rules only. Do not store secrets, passwords, private credentials, or temporary emotional comments. Keep entries short and actionable.

When the user explains why a normalization/linking result is wrong, treat the example as operator teaching data. Extract the reusable rule behind it, check whether existing catalogue facts/aliases/rules already express it, then update code, DB-backed knowledge, tests, and docs only as far as needed. Prefer global or brand/supplier-scoped logic over a one-row exception when the reasoning applies to similar products.

Every new normalization, linking, parser, importer, catalogue, alias, or KB rule must be documented in the repo in the same task, no matter which computer or chat applies it. Documentation is part of the change, not an optional follow-up. If the change creates executable knowledge through migrations, database rows, or code, also add the durable reasoning to the relevant docs so future sessions can understand why the rule exists.

Use these destinations:
- `docs/UI_DESIGN_SYSTEM.md` for design rules.
- `docs/DOMAIN_MODEL.md` for business rules.
- `docs/DECISIONS.md` for architecture/product decisions.
- `docs/CODEX_TASKS.md` for current priorities and lessons learned.
- `docs/REPO_MAP.md` for file placement or ownership rules.
- `AGENTS.md` for high-level agent behavior rules.
- `assistant_linking/docs/assistant_learning_design.md` for assistant normalization/linking learning philosophy, live-KB scan protocol, and reusable operator-teaching patterns.
- `assistant_linking/docs/live_kb_learning_map.md` for the latest documented production KB/rule inventory and cross-computer assistant-learning protocol.

Future entry templates:

```text
Design rule entry:
- YYYY-MM-DD - Rule: [short UI rule]. Example: [where to copy the pattern from].

Domain rule entry:
- YYYY-MM-DD - Rule: [business meaning or distinction]. Applies to: [models/flow].

Decision entry:
## YYYY-MM-DD - [Decision title]
Status: Accepted
Context: [why this came up]
Decision: [what future tasks should preserve]
Consequences: [what to watch]

Lesson learned entry:
- YYYY-MM-DD - Area: [short lesson/risk/priority]. Source: [task, bug, or user correction].

File placement rule entry:
- YYYY-MM-DD - Rule: Put [kind of code] in [owner/path], not [wrong place].
```

## Where to start reading

Start with:
- `README.md` for setup, routes, data model overview, deployment, and known constraints.
- `docs/REPO_MAP.md` for app ownership and file placement.
- `docs/DOMAIN_MODEL.md` for business terms and what not to confuse.
- `PROJECT_HANDOFF.md` for detailed operational background.
- `assistant_linking/docs/assistant_learning_design.md` before changing assistant parsing, aliases, catalogue imports, or learning flows.

Use the large prompt/audit files as references, not as primary docs:
- `AUDIT_AND_CODEX_PROMPTS.md`
- `KB_ALIASES_AND_CODEX_PROMPTS.md`

## Important directories and apps

- `perfumex/` - Django settings, root URLs, ASGI/WSGI.
- `prices/` - main app for suppliers, mailboxes, imports, price snapshots, viewer/staff pages, internal `OurProduct`, and most management commands.
- `catalog/` - canonical catalogue facts: brands, perfumes, variants, notes, accords, sources, claims, drafts.
- `assistant_core/` - staff assistant dashboard, editable knowledge/rules, catalogue CRUD/import, brand-watch research, claims, drafts.
- `assistant_linking/` - supplier-name parsing, aliases, Fragrantica staging, normalization queues, match groups, manual link decisions, link suggestions.
- `prices/templates/`, `assistant_core/templates/`, `assistant_linking/templates/` - server-rendered UI using `prices/base.html`.
- `prices/static/prices/` and `assistant_linking/static/assistant_linking/` - CSS/JS assets.
- `scripts/` - local maintenance, DB sync, and repository check scripts.
- `docs/` - short agent-maintained repository documentation.

## Architecture rules

- Inspect the existing owner in `docs/REPO_MAP.md` before adding files, routes, models, forms, services, commands, templates, or static assets.
- Do not duplicate business logic across views, templates, management commands, or scripts.
- Prefer services/helpers for reusable business logic; keep views focused on orchestration, permissions, and response rendering.
- Keep templates presentation-only where possible. Do not hide durable business rules in template conditionals.
- Keep imports/parsing/linking/catalog logic separated from UI: import logic belongs in `prices`, parser/linking logic in `assistant_linking`, catalogue facts in `catalog`, and assistant research/knowledge UI in `assistant_core`.

## Commands to run

Common checks:

```bash
python manage.py check
python manage.py test
make ci
```

Before pushing to `main` or triggering deploy, run the GitHub-equivalent gate:

```bash
make deploy-gate
```

`make local-smoke` is a fast iteration guard, not a deploy gate. `make ui-smoke` is the focused guard for template, CSS, JavaScript, responsive table, accessibility, and shared UI partial changes.

Targeted checks:

```bash
ruff check .
black --check .
djlint --check prices/templates assistant_core/templates assistant_linking/templates
npm run lint:js
make ui-smoke
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python scripts/check_agent_docs.py
python scripts/check_agent_docs_rules.py
python scripts/check_css_static.py
python scripts/check_css_static_rules.py
python scripts/check_destructive_actions.py
python scripts/check_destructive_actions_rules.py
python scripts/check_management_commands.py
python scripts/check_management_commands_rules.py
python scripts/check_doc_drift.py
python scripts/check_doc_drift_rules.py
python scripts/check_js_dom_safety.py
python scripts/check_js_dom_safety_rules.py
python scripts/check_js_accessibility.py
python scripts/check_js_accessibility_rules.py
python scripts/check_js_table_labels.py
python scripts/check_js_table_labels_rules.py
python scripts/check_js_syntax.py
python scripts/check_js_syntax_rules.py
python scripts/check_local_smoke.py
python scripts/check_local_smoke_rules.py
python scripts/check_make_targets.py
python scripts/check_make_targets_rules.py
python scripts/check_markdown_links.py
python scripts/check_markdown_links_rules.py
python scripts/check_migration_graph.py
python scripts/check_migration_graph_rules.py
python scripts/check_python_syntax.py
python scripts/check_python_syntax_rules.py
python scripts/check_secret_patterns.py
python scripts/check_secret_patterns_rules.py
python scripts/check_service_imports.py
python scripts/check_service_imports_rules.py
python scripts/check_static_references.py
python scripts/check_static_references_rules.py
python scripts/check_table_mobile.py
python scripts/check_table_mobile_rules.py
python scripts/check_table_headers.py
python scripts/check_table_headers_rules.py
python scripts/check_template_accessibility.py
python scripts/check_template_accessibility_rules.py
python scripts/check_template_buttons.py
python scripts/check_template_buttons_rules.py
python scripts/check_template_csrf.py
python scripts/check_template_csrf_rules.py
python scripts/check_template_drawers.py
python scripts/check_template_drawers_rules.py
python scripts/check_template_ids.py
python scripts/check_template_ids_rules.py
python scripts/check_template_inline_styles.py
python scripts/check_template_inline_styles_rules.py
python scripts/check_template_labels.py
python scripts/check_template_labels_rules.py
python scripts/check_template_links.py
python scripts/check_template_links_rules.py
python scripts/check_template_layout.py
python scripts/check_template_layout_rules.py
python scripts/check_templates.py
python scripts/check_templates_rules.py
python scripts/check_template_urls.py
python scripts/check_template_urls_rules.py
python scripts/check_urls.py
python scripts/check_urls_rules.py
python scripts/check_ui_partials.py
python scripts/check_ui_partials_rules.py
python scripts/check_view_exports.py
python scripts/check_view_exports_rules.py
```

Local run:

```bash
run_python_server.cmd
```

Important: the helper runs Django with `--noreload`, so restart it after Python or template changes. The app is PostgreSQL-only even though `db.sqlite3` exists in the worktree.

## Rules for safe changes

- Keep changes narrow and behavior-focused.
- Do not refactor while fixing unrelated behavior.
- Preserve existing user or generated work in the tree.
- Check migrations before changing models; do not change models for one-off data corrections.
- Prefer services for reusable domain behavior; keep views responsible for orchestration and presentation.
- Keep import/email/linking changes transactional and auditable where data can be mutated.
- Treat `SupplierProduct <- PriceSnapshot <- ImportBatch <- ImportFile` as the critical audit chain.
- Do not silently publish assistant output. Assistant features should expose review, confidence, and audit trail.

## Rules for UI consistency

- Read `docs/UI_DESIGN_SYSTEM.md` before editing templates, CSS, or JS.
- Reuse `prices/base.html`, page stacks, page headers, section cards/panels, shared pagination, tab, table-empty, button, flash, drawer, and table patterns.
- When adding tabs or pagination, first find existing examples and reuse their classes/structure.
- Keep responsive behavior consistent with existing `table-mobile`, scroll wrappers, drawers, and media breakpoints.
- Do not invent a new visual style if an existing pattern exists.

## Rules for updating docs

Future tasks must check whether docs need updating before the final response.

The repository memory is the checked-in documentation, not the chat history. When a correction or decision should guide later work, write the shortest useful version into the relevant file.

Update docs only for durable knowledge:
- a new architectural ownership rule,
- a new domain/business distinction,
- a repeated correction from the user,
- a lasting UI pattern,
- a meaningful decision,
- a new operational risk or check.

For repeated user corrections, add the rule in the smallest relevant place:
- `docs/WORKING_RULES.md` for workflow/change discipline,
- `docs/UI_DESIGN_SYSTEM.md` for visual or interaction consistency,
- `docs/DOMAIN_MODEL.md` for business terminology,
- `docs/DECISIONS.md` for architectural/business/design decisions,
- `docs/REPO_MAP.md` for app ownership or file placement,
- `docs/CODEX_TASKS.md` for active risks, priorities, mistakes, or lessons.

## Rules for updating docs after repeated corrections

If the same user correction appears twice, convert it into a durable rule instead of relying on memory. Add the shortest useful note to the relevant doc, keep it practical, and mention the update in the task summary.
