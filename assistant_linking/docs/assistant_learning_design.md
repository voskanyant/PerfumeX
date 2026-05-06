# Assistant Learning Design

This note describes how the assistant should be extended over time.

## Core Principle

The assistant should learn through data, rules, aliases, catalogue facts, and review decisions. Do not hardcode one-off brand/product fixes into parser logic when the same result can be represented as knowledge.

User corrections are training examples for reusable perfume-domain rules. When an operator explains why a parse, link, or display is wrong, extract the smallest durable rule behind the example and implement that rule through the safest available knowledge surface. Only make product-specific knowledge when the reason is truly product-specific; otherwise make the rule global or brand/supplier scoped as appropriate.

Code should provide reusable machinery:

- parse supplier text into stable fields
- apply aliases and approved rules
- compare supplier rows with catalogue rows
- show review queues and impact previews
- write audit trails for human decisions
- import external catalogue facts through documented extractors

Knowledge should provide business meaning:

- brand aliases
- product aliases
- collection names
- concentration aliases
- audience aliases
- regex preprocess rules
- garbage/exclusion keywords
- catalogue `Brand` and `Perfume` rows
- manual link decisions and review outcomes

## Preferred Change Order

When a supplier product parses incorrectly, use this order:

1. Add or correct catalogue data if the product exists as a real fragrance.
2. Add a `BrandAlias` if the brand text is not recognized.
3. Add a `ProductAlias` if supplier text needs to map to a canonical fragrance name or collection.
4. Add a `ConcentrationAlias` if concentration text is supplier-specific or missing from defaults.
5. Add a `GlobalRule` only for reusable parser terms, regex preprocessing, audience terms, or garbage keywords.
6. Change parser code only when the parser lacks a reusable capability or a general rule cannot express the behavior.

For each correction, write down the learned rule in one of these places when it should survive across computers:

- `docs/DOMAIN_MODEL.md` for business meaning and parser/linking behavior.
- `assistant_linking/docs/assistant_learning_design.md` for assistant-learning workflow and operator teaching philosophy.
- `docs/DECISIONS.md` for durable architecture or product decisions.
- `docs/CODEX_TASKS.md` for active lessons, risks, or repeated mistakes.
- Seed migrations or editable DB knowledge for aliases/rules that must execute in production.

This is mandatory for every new assistant normalization, linking, parser,
importer, catalogue, alias, and KB rule. The executable change and the durable
reasoning must travel together, across all computers and chats. A future agent
should be able to read the docs and understand why the rule exists before
looking at the exact migration or code.

## What Belongs In Code

Code changes are appropriate for:

- new extraction formats, such as saved HTML catalogue pages
- generic parsing capabilities, such as release-year extraction
- safer matching algorithms
- review screens and operator workflows
- validation, audit logging, and impact previews
- performance improvements for large parse/reparse jobs

Code changes are not appropriate for:

- one brand's collection name
- one fragrance spelling
- one supplier's typo
- one concentration phrase that can be an alias
- one exclusion keyword that can be a rule

Those should live in database-backed knowledge or seed migrations.

## Live Knowledge Scan Protocol

Before changing normalization, linking, or assistant learning on a restored/live-like database, inspect existing knowledge first:

- catalogue brands, perfumes, collections, variants, and reviewed Fragrantica links
- `BrandAlias`, `ProductAlias`, and `ConcentrationAlias`
- approved `GlobalRule` and `SupplierRule` rows
- `KnowledgeNote`, manual link decisions, AI recommendations, and learning proposals when relevant
- existing migrations that seed the same kind of rule

Use the live KB to avoid duplicating rules, to find whether a correction should be global, brand-scoped, supplier-scoped, or catalogue-confirmed, and to choose test fixtures that represent the real failure. Do not store passwords, mailbox secrets, or temporary credentials in docs.

The latest documented production KB/rule inventory and cross-computer learning
protocol lives in `assistant_linking/docs/live_kb_learning_map.md`.

## Current Learned Patterns

These rules came from operator corrections and should guide future logic:

- Fix the general rule, not only the shown row. If the example is `Extrait`, teach `Extrait` as a concentration alias for `Extrait de Parfum`, not as a hardcoded `Crystal Saffron` exception.
- Catalogue-confirmed identity is stronger than supplier wording. Supplier descriptors like new, exclusive, red, Cyrillic color notes, old/new design, with cap, no box, damaged, tester, sample, decant, atomizer, and similar terms should be stripped, moved to structured type/packaging/comment fields, or routed to manual review according to the existing rule surface.
- Catalogue-confirmed scent names can include words that are also parser metadata terms. Preserve a term such as `Vintage` in the scent name when an exact same-brand catalogue scent match exists, instead of routing that row to the metadata category.
- Brand, brand-scoped collection, scent, concentration, size, audience, tester, packaging, type, year, and comments are separate fields. Do not leave metadata in the scent name just because the supplier wrote it near the scent.
- Leading brand text is stronger than later supplier comparison comments. If a parenthetical comment names another known brand, strip that comment from the scent, preserve the leading brand, and surface manual review rather than auto-linking to the comparison brand.
- Audience words belong in the scent name only when the catalogue has same-base men/women scents or the reviewed local name itself includes the audience suffix. Otherwise keep audience in the audience field only.
- Fragrantica and Our Products should improve each other through reviewed links and catalogue-confirmed matching, but Fragrantica rows are evidence until linked; they should not silently overwrite local catalogue facts.
- Ambiguous corrections should produce warnings or manual-review candidates instead of confident automatic matches.

## Catalogue Import Pattern

External catalogue pages should be treated as source data, not as parser code.

The current saved-HTML flow is:

1. Parse the external page or parsed catalogue export into structured rows: brand, collection, fragrance name, audience/gender, release year, source link.
2. Dry-run first and show the operator extracted rows, collections, counts, and sample rows before applying anything.
3. If accepted, stage external rows in `assistant_linking.FragranticaProduct`.
4. Review staged Fragrantica rows next to local `catalog.Perfume` rows.
5. Link or merge into `catalog.Perfume` only after review. Fragrantica can update brand/name/brand-scoped collection/audience/year through that reviewed link, but local concentration and variants are preserved. Our Products remains the source of truth used by supplier normalization.
   If Fragrantica/local catalogue evidence shows same-base men/women scents, keep the audience in the reviewed local scent name (`for Men`/`for Women` or `Pour Homme`/`Pour Femme`) and also keep the separate audience field.
6. When an approved Fragrantica link changes the local scent spelling, store the old local scent name as a `ProductAlias` for that brand so future matching uses knowledge instead of code.
7. Promote reviewed local Fragrantica links to production with the Fragrantica catalogue link export/import commands; Git deploys move code, not local database rows.
8. Create aliases or parser knowledge only from approved links/merges, not directly from raw HTML import.
9. Reparse affected supplier rows only after approved knowledge changes.

For Fragrantica-like saved brand pages, see `assistant_linking/docs/html_catalog_import_rules.md`.

For live-sized imports, upload saved brand HTML files or parsed Fragrantica JSON exports to the server and use the
folder management command. Run it without `--apply` first, then rerun with
`--apply` only after the counts look right.

## AI Learning Target

Later AI-backed assistant features should read from the same knowledge surfaces instead of inventing private behavior:

- Use catalogue rows as the canonical product universe.
- Use catalogue collection facts to enrich normalization when brand, scent, and concentration match unambiguously. Supplier, size, tester, and packaging should not affect that collection lookup.
- Use parser variant-type terms for packaging/type words that should be stripped from scent names and stored as parsed type, such as `woodbox`.
- Use staged external catalogue rows as evidence and merge candidates, not as canonical products by themselves.
- Use aliases as explainable normalization rules.
- Use `GlobalRule` rows as editable parser policy.
- Use manual decisions as supervised examples.
- Use missing reports to identify catalogue gaps.
- Surface uncertainty and conflicts instead of auto-publishing.

The AI should propose knowledge changes for staff review. It should not silently mutate product identity, overwrite links, or bypass the same rule/alias/catalogue layers used by deterministic parsing.

Safe rollout order:

1. Fragrantica link advisor: deterministic matching builds the candidate list, AI may rerank/explain only those candidate IDs, and staff chooses whether to link.
2. Review pattern finder: AI summarizes repeated manual decisions and proposes aliases/rules for review when the same correction appears many times.
3. Knowledge proposal drafts: AI creates pending alias/rule/catalogue suggestions with source rows and expected impact, but approval remains a staff action.
4. Parser review helper: AI can explain low-confidence parses or Cyrillic unresolved identity, but saved parses still come from parser code/rules unless staff accepts a reviewed knowledge change.

AI recommendation records should include the input context, prompt version, model name, confidence, risk level, reasoning, and status. They are audit evidence, not product data. Staff review them through the AI recommendations queue. Accepted recommendations create pending learning proposals; those proposals are still not live aliases, rules, catalogue facts, or links until staff explicitly applies them through the normal audited workflow. The first supported apply path is Fragrantica link review, which reuses the existing reviewed Fragrantica linking action.

The first review pattern finder is deterministic and does not require an API key: it scans repeated approved manual supplier-product links and can propose a `ProductAlias` when the same parsed supplier scent text is repeatedly linked to a different catalogue perfume name. It can also propose a `BrandAlias` when repeated reviewed links show the same non-generic detected supplier brand text should map to a different catalogue brand. Proposals remain pending until staff accepts and applies them; applying creates alias knowledge only and does not automatically reparse saved supplier rows. Alias proposals should show a saved-parse impact preview before apply, including matched saved parses, active supplier rows, unlocked parses, and sample supplier names. After staff applies an alias proposal, any parse refresh must remain explicit and targeted to the preview-matched saved parses; human-locked parses are skipped.

The AI recommendation queue may bulk-apply accepted pending alias proposals from the visible page only. This shortcut is limited to `ProductAlias` and `BrandAlias` proposals because they create reviewable knowledge. Fragrantica link proposals must stay one-at-a-time reviewed actions in the linking workbench. Alias proposal detail pages should compare the saved impact preview against current matching saved parses and warn when the counts have changed. Applying an alias proposal must also refuse stale or missing impact previews so knowledge is not created from outdated evidence. Pending alias proposals must expose an explicit regenerate-preview action that refreshes the saved impact counts and samples before apply. Applied alias proposals must keep the created alias ID in `impact_json` and expose a revert action that deactivates only that created alias; saved parses still require an explicit refresh after either apply or revert.

## Operational Rule

Every new assistant learning workflow should answer these questions in code or docs:

- What source data was read?
- What reusable rule or catalogue fact was created?
- Which supplier rows were affected?
- How can the operator preview the impact before applying it?
- How can the change be reversed or audited?

If a change cannot answer those questions, it is probably too hidden to be a safe assistant learning feature.
