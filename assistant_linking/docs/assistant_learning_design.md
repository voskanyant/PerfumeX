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

1. Parse the external page into structured rows: brand, collection, fragrance name, release year, source path.
2. Dry-run first and compare extracted rows with local `catalog.Perfume`.
3. Write a missing report for operator review.
4. If accepted, create or update catalogue rows.
5. Create aliases that teach the normalizer how supplier names map to catalogue names and collections.
6. Reparse only the affected supplier rows unless intentionally rebuilding all parses.

For Fragrantica-like saved brand pages, see `assistant_linking/docs/html_catalog_import_rules.md`.

## AI Learning Target

Later AI-backed assistant features should read from the same knowledge surfaces instead of inventing private behavior:

- Use catalogue rows as the canonical product universe.
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
