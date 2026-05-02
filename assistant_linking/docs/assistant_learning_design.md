# Assistant Learning Design

This note describes how the assistant should be extended over time.

## Core Principle

The assistant should learn through data, rules, aliases, catalogue facts, and review decisions. Do not hardcode one-off brand/product fixes into parser logic when the same result can be represented as knowledge.

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

## Catalogue Import Pattern

External catalogue pages should be treated as source data, not as parser code.

The current saved-HTML flow is:

1. Parse the external page or parsed catalogue export into structured rows: brand, collection, fragrance name, audience/gender, release year, source link.
2. Dry-run first and show the operator extracted rows, collections, counts, and sample rows before applying anything.
3. If accepted, stage external rows in `assistant_linking.FragranticaProduct`.
4. Review staged Fragrantica rows next to local `catalog.Perfume` rows.
5. Link or merge into `catalog.Perfume` only after review. Fragrantica can update brand/name/brand-scoped collection/audience/year through that reviewed link, but local concentration and variants are preserved. Our Products remains the source of truth used by supplier normalization.
6. Promote reviewed local Fragrantica links to production with the Fragrantica catalogue link export/import commands; Git deploys move code, not local database rows.
7. Create aliases or parser knowledge only from approved links/merges, not directly from raw HTML import.
8. Reparse affected supplier rows only after approved knowledge changes.

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

## Operational Rule

Every new assistant learning workflow should answer these questions in code or docs:

- What source data was read?
- What reusable rule or catalogue fact was created?
- Which supplier rows were affected?
- How can the operator preview the impact before applying it?
- How can the change be reversed or audited?

If a change cannot answer those questions, it is probably too hidden to be a safe assistant learning feature.
