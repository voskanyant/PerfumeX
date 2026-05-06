# Live KB Learning Map

This note is a production-shaped memory map for future Codex sessions. It does
not contain credentials. If live access is needed again, ask the operator or use
the active session context; never write passwords, mailbox secrets, or private
tokens into repo docs.

Last read-only scan: 2026-05-06 against the production project at
`/opt/perfumex/PerfumeX`, branch `main`, commit `b7a6f06`.

## Why This Exists

Operator corrections are not just fixes for one row. They are examples that
teach how perfume identity should be modeled. Before adding code, check what
the live knowledge base already knows, then choose the smallest durable surface
that can express the rule.

Use this alongside:

- `AGENTS.md`
- `docs/DOMAIN_MODEL.md`
- `assistant_linking/docs/assistant_learning_design.md`
- `assistant_linking/docs/html_catalog_import_rules.md`

## Live Knowledge Shape

Read-only production counts from the scan:

- Catalogue: 1,011 brands, 3,481 collections, 16,228 perfumes, 38,474 variants.
- Suppliers: 69 suppliers, 455,235 supplier products, 144,654 active supplier products.
- Assistant parses: 218,098 parsed supplier products.
- Knowledge rows: 1,279 active brand aliases, 16,639 active product aliases, 44 active concentration aliases.
- Parser rules: 131 global rules, 129 active approved global rules.
- Supplier-scoped rules: 0.
- Review data: 124,480 Fragrantica products, 9,660 reviewed Fragrantica links.
- AI data: 5 accepted AI recommendations, 5 pending AI learning proposals.
- Manual link decisions and `KnowledgeNote` rows were empty in this scan.

The big lesson is that production already has a lot of alias and parser
knowledge, but supplier-scoped rules and knowledge notes are unused. Prefer
checking existing aliases/rules first, then add focused data or seed migrations
instead of duplicating behavior in parser code.

## Existing Rule Surfaces

Active approved global rules by kind in the live scan:

- `garbage_keyword`: supplier-only words that should not become product identity.
- `parser_audience_term`: audience/gender terms such as men, women, woman, male, female, pour homme, pour femme, uomo, donna, unisex, lady, her, him, and related Cyrillic variants.
- `parser_tester_term`: tester terms, including messy supplier spellings.
- `parser_decant_term`, `parser_sample_term`, `parser_mini_term`, `parser_travel_term`, `parser_atomizer_term`: variant/type terms.
- `parser_no_box_packaging_term`, `parser_with_cap_packaging_term`, `parser_old_design_packaging_term`, `parser_refillable_packaging_term`, `parser_dented_packaging_term`: packaging/comment terms.
- `parser_set_term`, `parser_vintage_term`, `parser_refill_term`, `parser_supplier_comment_term`: additional structured metadata terms.
- `regex_preprocess`: cleanup rules for supplier spelling before parsing.

Examples seen in live rules include garbage/damage words such as `подмят`,
`подмятый`, `помят`, `поврежд`, `fake`, `old design`, `old box`, `vintage`,
`потерт`, `потёрт`, and `по мотивам`; atomizer terms such as `atomiser`,
`atomizer`, `атомайзер`; and decant terms such as `отлив`, `отливант`, and
`отлив из флакона`.

## Warning Signals

Top warnings sampled from parsed rows:

- `brand missing`
- `gender missing`
- `concentration missing`
- `size ambiguous`
- detected but risky identity words such as intense, sport, extreme, fraiche, elixir, absolu, mini
- catalogue concentration conflicts, for example catalogue suggests Eau de Parfum while supplier text parsed as Eau de Toilette or Extrait de Parfum
- `product name missing`
- excluded garbage keywords

Treat these warnings as discovery queues. A repeated warning should become a
catalogue correction, alias, parser rule, or explicit manual-review rule only
after the reason is understood.

## Choosing The Knowledge Surface

When the operator explains a correction:

1. Identify the reusable reason, not just the shown row.
2. Check live/local catalogue facts and existing aliases/rules.
3. Decide whether the rule is global, brand-scoped, supplier-scoped, or catalogue-confirmed.
4. Use data first: `BrandAlias`, `ProductAlias`, `ConcentrationAlias`, catalogue rows, collection rows, Fragrantica links, or approved rules.
5. Change parser code only when existing knowledge surfaces cannot express the behavior safely.
6. Add tests for the general pattern and at least one "do not over-apply" case when the rule could be risky.
7. Update docs when the learned rule should survive across computers.

Step 7 is mandatory for every new normalization, linking, parser, importer,
catalogue, alias, and KB rule. Do not leave the reasoning only in chat, a
database row, or a migration name. Put the durable lesson in repo docs during
the same task so another computer can continue the work without rediscovering
the logic.

Good examples:

- `Extrait` in a Fragrantica title is a concentration alias for `Extrait de Parfum`; do not hardcode one perfume.
- If catalogue confirms only one scent identity and supplier words like `NEW!!!`, bottle color, or `exclusive` are supplier garbage, strip or comment those words instead of keeping them in scent.
- If a brand has same-base men/women products, audience can be part of the scent name. If the catalogue has only one scent, keep audience in the audience field only.
- If brand and scent are self-titled, keep the internal identity when catalogue confirms it, but display can collapse duplicate brand/scent for readability.

## Script And Command Surfaces

Useful assistant/catalogue commands:

- `assistant_linking/management/commands/reparse_supplier_products.py`
- `assistant_linking/management/commands/rebuild_match_groups.py`
- `assistant_linking/management/commands/refresh_normalization_stats.py`
- `assistant_linking/management/commands/import_brand_catalog_html.py`
- `assistant_linking/management/commands/import_brand_catalog_folder.py`
- `assistant_linking/management/commands/export_fragrantica_catalogue_links.py`
- `assistant_linking/management/commands/import_fragrantica_catalogue_links.py`
- `prices/management/commands/bulk_link_catalogue_filtered.py`

Useful import-price commands:

- `prices/management/commands/import_emails.py`
- `prices/management/commands/process_email_runs.py`
- `prices/management/commands/import_supplier_folder.py`
- `prices/management/commands/repair_supplier_price_imports.py`
- `prices/management/commands/cleanup_duplicate_price_imports.py`
- `prices/management/commands/cleanup_import_media.py`
- `prices/management/commands/reorganize_import_files.py`
- `prices/management/commands/sync_cbr_rates.py`
- `prices/management/commands/run_rq_worker.py`

Use dry-run modes where available. For production, prefer read-only inspection
first, then a targeted management command with counts and samples.

## Seed Migration Memory

Many parser and KB lessons are already seeded through migrations. Before adding
a new rule, scan `assistant_core/migrations` and `assistant_linking/migrations`
for similar seed data.

Important seed themes already present:

- default parser KB rules
- audience aliases, including compact `(L)`/`(M)` style terms
- sample, mini, tester, decant, atomizer, refill, travel, set, vintage terms
- damaged packaging and no-box/with-cap/old-design packaging terms
- regex preprocessing for apostrophes, edition/exclusive forms, and bracketed audience terms
- brand/product/concentration aliases
- Fragrantica import/linking models and diacritic/apostrophe normalization
- AI recommendation and learning proposal models

## Cross-Computer Protocol

When resuming this project on another machine:

1. Pull `main`.
2. Read `AGENTS.md`, `docs/DOMAIN_MODEL.md`, this file, and `assistant_linking/docs/assistant_learning_design.md`.
3. If live access is available, run a read-only Django shell inventory before changing normalization/linking behavior.
4. Compare the live KB with local migrations and tests.
5. Add durable rules to repo docs and executable knowledge to migrations/code/tests as needed.
6. Do not store server credentials in git.

This is the project memory bridge: chat teaches the next correction, docs keep
the reasoning, and aliases/rules/catalogue rows make the behavior executable.
