# Working Rules

## Purpose of this document

Practical workflow rules for making small, safe changes in this repository. Use this with [AGENTS.md](../AGENTS.md) before non-trivial implementation work.

Related docs: [README.md](../README.md), [docs/REPO_MAP.md](REPO_MAP.md), [docs/DOMAIN_MODEL.md](DOMAIN_MODEL.md), [docs/UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md), [docs/DECISIONS.md](DECISIONS.md), [docs/CODEX_TASKS.md](CODEX_TASKS.md), [docs/DRIFT_CHECKLIST.md](DRIFT_CHECKLIST.md).

## Small safe changes

- Read `AGENTS.md` and the relevant focused docs before editing.
- Before any non-trivial change, inspect `AGENTS.md`, `docs/REPO_MAP.md`, `docs/DOMAIN_MODEL.md`, and `docs/CODEX_TASKS.md`.
- If UI/templates/static files are touched, inspect `docs/UI_DESIGN_SYSTEM.md`.
- If architecture or business behavior is touched, inspect `docs/DECISIONS.md`.
- Identify the owning app before adding files or behavior.
- Keep the change scoped to the requested behavior.
- Reuse existing views, services, forms, templates, CSS classes, and tests as examples.
- Prefer targeted fixes over broad rewrites.
- Keep data-mutating changes auditable and reversible where practical.
- Preserve unrelated local changes and generated files.
- Normal future prompts should be short. The user should only need to say: "Read AGENTS.md first. [task]"

## Tests and checks

Add or update tests when:
- parser, importer, matching, or link behavior changes,
- model constraints/migrations change,
- a bug fix prevents a regression,
- a view/form/service changes business behavior,
- a management command changes data.

For documentation-only changes, a full test run is usually unnecessary. Run a lightweight check such as `git diff --check` when practical.

Before pushing to `main` or triggering deploy, run `make deploy-gate`. It mirrors the GitHub CI jobs that must pass before deploy. `make local-smoke` is only a fast iteration guard and is not enough for a main/deploy push.

If `make deploy-gate` cannot run because local dependencies or PostgreSQL are missing, stop and report the blocker instead of pushing to `main`.

## Model changes

Do not change models when:
- the task can be solved with existing alias/rule/catalogue data,
- the change is a one-off supplier or product correction,
- the migration impact is unclear,
- generated/runtime data would need manual repair and no plan exists.

If models must change, inspect existing migrations and tests first, add a migration, and document any new durable domain concept in `docs/DOMAIN_MODEL.md` or `docs/DECISIONS.md`.

## Business fixes

- Do not hardcode one-off brand/product/parser fixes in code when an alias, catalogue fact, or parser rule can represent them.
- Follow `assistant_linking/docs/assistant_learning_design.md` for assistant learning changes.
- For wrong supplier parsing, prefer this order: catalogue data, brand alias, product alias, concentration alias, global/supplier rule, then parser code only for reusable capability gaps.
- When parser rules, aliases, catalogue-backed normalization behavior, or seeded assistant knowledge change saved parse output, bump `PARSER_VERSION` and run a tightly bounded production reparse for exact affected supplier-name patterns; broad terms and full-table reparses are too large for the deploy SSH timeout.
- Do not auto-approve assistant suggestions or external catalogue rows.

## Supplier history preparation

- 2026-05-01 - Rule: When a supplier changed spreadsheet structure and the user chooses normalization for price-history import, split source files into layout/date folders, create a fresh final supplier folder, and normalize only files whose layout does not match the latest/current mapping. Files already using the current layout must be copied/renamed without rewriting workbook contents. Keep unknown layouts or failed transformations suspicious.

## Documentation updates

After any change, check whether documentation should be updated.

Update docs when a change creates durable knowledge:
- app ownership or file-placement rule,
- business/domain distinction,
- recurring user correction,
- design-system pattern,
- architectural/business/design decision,
- current risk or priority.

The repo memory lives in files, not in chat. If the lesson should affect future work, record it in the smallest relevant doc.

Destination map:
- UI/design corrections: `docs/UI_DESIGN_SYSTEM.md`
- business/domain meaning: `docs/DOMAIN_MODEL.md`
- architecture decisions: `docs/DECISIONS.md`
- file placement or app ownership: `docs/REPO_MAP.md`
- workflow or repeated Codex behavior: `AGENTS.md` or `docs/WORKING_RULES.md`
- current task memory, active warnings, repeated mistakes, or lessons: `docs/CODEX_TASKS.md`

Docs must stay short:
- Do not dump long chat transcripts.
- Add short durable rules.
- Prefer examples over long explanations.
- Do not duplicate large existing docs; add a short summary and link to the source.

## Self-documenting memory protocol

The repo memory lives in files, not in chat.

When the user says "remember", "always", "never", "from now on", "same as before", or corrects a repeated mistake, consider whether a durable project rule should be added to the relevant doc. If the user explicitly asks to make it permanent, update the relevant doc in the same task.

Store durable project rules only. Do not store secrets, passwords, private credentials, or temporary emotional comments. Keep entries short and actionable.

Use these destinations:
- `docs/UI_DESIGN_SYSTEM.md` for design rules.
- `docs/DOMAIN_MODEL.md` for business rules.
- `docs/DECISIONS.md` for architecture/product decisions.
- `docs/CODEX_TASKS.md` for current priorities and lessons learned.
- `docs/REPO_MAP.md` for file placement or ownership rules.
- `AGENTS.md` for high-level agent behavior rules.

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

## Repeated user corrections

When the user corrects the same kind of issue more than once:
1. Identify whether it is workflow, domain, UI, architecture, or priority.
2. Add a short rule to the smallest relevant doc.
3. Mention the docs update in the final task summary.
4. Avoid broad rewrites unless the correction reveals a systemic bug.
