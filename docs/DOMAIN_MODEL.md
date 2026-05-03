# Domain Model

## Purpose of this document

Current business glossary and domain distinctions for PerfumeX. Use this before changing models, parser behavior, imports, linking, catalogue concepts, assistant knowledge, or any business-facing behavior.

Related docs: [AGENTS.md](../AGENTS.md), [README.md](../README.md), [docs/REPO_MAP.md](REPO_MAP.md), [docs/DECISIONS.md](DECISIONS.md), [docs/WORKING_RULES.md](WORKING_RULES.md), [docs/CODEX_TASKS.md](CODEX_TASKS.md), [docs/DRIFT_CHECKLIST.md](DRIFT_CHECKLIST.md), [PROJECT_HANDOFF.md](../PROJECT_HANDOFF.md).

## Core business terms

- Supplier - external vendor that sends or hosts price lists. Model: `prices.Supplier`.
- Mailbox - IMAP account scanned for supplier attachments. Model: `prices.Mailbox`.
- Supplier mailbox rule - mailbox-specific matching override for sender, subject, and filename. Model: `prices.SupplierMailboxRule`.
- Price source - supplier-hosted URL or email-link source for downloadable price files. Model: `prices.SupplierPriceSource`.
- Supplier file mapping - spreadsheet parsing configuration for a supplier/file kind. Model: `prices.SupplierFileMapping`.
- Import batch - one import event for one supplier, usually from manual upload or email. Model: `prices.ImportBatch`.
- Import file - one file/attachment inside an import batch, with parse status, storage/quarantine info, and content hash. Model: `prices.ImportFile`.
- Supplier product - current offer row from a supplier: raw-ish supplier name/SKU, current price/stock, active state, and optional links to internal/canonical products. Model: `prices.SupplierProduct`.
- Price snapshot - historical price observation for a supplier product. `price` and `currency` preserve the source file's original amount and currency; `price_rub` and `price_usd` are converted display/history values. Model: `prices.PriceSnapshot`.
- Stock snapshot - historical stock observation for a supplier product. Model: `prices.StockSnapshot`.
- Internal product / Our Product - older internal grouping entity used by the price workspace. Model: `prices.OurProduct`.
- Catalogue brand - canonical perfume brand. Model: `catalog.Brand`. Renaming a catalogue brand changes the displayed brand on all linked catalogue perfumes because perfumes reference the brand by foreign key; use a separate merge action for combining two existing brands.
- Catalogue collection - brand-scoped perfume line/collection. The same collection name under different brands is a different collection. Model: `catalog.Collection`.
- Catalogue perfume - canonical fragrance identity: brand, name, concentration, audience, collection, release year, publication/verification fields. Model: `catalog.Perfume`.
- Catalogue variant - sellable/package variant of a canonical perfume: size, packaging, tester/type, EAN/SKU. Model: `catalog.PerfumeVariant`.
- Fact claim - reviewable catalogue fact/evidence extracted by research. Model: `catalog.FactClaim`.
- AI draft - reviewable generated catalogue text. Model: `catalog.AIDraft`.

## Assistant and linking terms

- Alias - editable knowledge that maps supplier/external text to canonical meaning. Models: `assistant_linking.BrandAlias`, `ProductAlias`, `ConcentrationAlias`.
- Global rule - reusable assistant policy such as parser terms, regex preprocessing, audience terms, or garbage keywords. Model: `assistant_core.GlobalRule`.
- Supplier rule - supplier-scoped assistant rule. Model: `assistant_core.SupplierRule`.
- Knowledge note - operator-authored assistant note. Model: `assistant_core.KnowledgeNote`.
- Parsed supplier product - deterministic parse output for one supplier product: brand, scent/name, collection, concentration, size, packaging/type, warnings, confidence, parser version, and human lock. Model: `assistant_linking.ParsedSupplierProduct`.
- Unparsed supplier product - a `SupplierProduct` with no saved `ParsedSupplierProduct` row yet. Temporary parser previews do not move a row out of Unparsed; only saved parses do.
- Normalization stats snapshot - cached counts for parser queues and issue categories. Model: `assistant_linking.NormalizationStatsSnapshot`.
- Match group - grouped parsed products that appear to refer to the same catalogue item/variant. Model: `assistant_linking.MatchGroup`.
- Match group item - supplier product membership in a match group. Model: `assistant_linking.MatchGroupItem`.
- Manual link decision - staff approval/rejection/link decision for a supplier row. Model: `assistant_linking.ManualLinkDecision`.
- Manual link decision audit - record of replaced manual decisions. Model: `assistant_linking.ManualLinkDecisionAudit`.
- Link action - undoable bulk/link operation payload. Model: `assistant_linking.LinkAction`.
- Link suggestion - candidate link from deterministic/mock/OpenAI suggestion engines. Model: `assistant_linking.LinkSuggestion`.
- Fragrantica product - staged external catalogue row from saved HTML or parsed catalogue JSON/CSV import; source evidence that can be linked to `catalog.Perfume` after staff review. Brand/name/brand-scoped collection/audience/year/source URL may update or support the local catalogue through that reviewed link; concentration and variants remain local catalogue data. Model: `assistant_linking.FragranticaProduct`.
- Brand watch profile - configured research target for a brand. Model: `assistant_core.BrandWatchProfile`.
- Source snapshot - captured source/evidence from brand research. Model: `assistant_core.SourceSnapshot`.
- Detected change - reviewable research delta. Model: `assistant_core.DetectedChange`.

## Important flows

Price import:
1. Match supplier and file/mapping.
2. Create `ImportBatch` and `ImportFile`.
3. Parse spreadsheet rows.
4. Upsert `SupplierProduct`.
5. Create `PriceSnapshot` and optional `StockSnapshot`.
6. Mark untouched products inactive when appropriate.

Normalization and linking:
1. Parse `SupplierProduct.name` into `ParsedSupplierProduct`.
   Parser/KB changes only affect existing saved parses after `PARSER_VERSION` changes and affected stale unlocked rows are reparsed.
2. Apply catalogue facts, aliases, concentration aliases, parser rules, and garbage terms.
3. Surface low-confidence/missing/conflict rows in normalization queues.
4. Treat complete parse identity as separate from catalogue/link evidence; a clean parse can still need catalogue or link review.
5. Treat damaged/no-cellophane packaging phrases as structured dented packaging terms (`parser_dented_packaging_term`), not as garbage exclusions. Treat positive supplier cap phrases such as `с крышкой` and abbreviations such as `c фирм. крыш` as `With Cap` packaging terms, not removable comments or scent-name tokens. Treat old-design abbreviations such as `ст.ди` as structured packaging terms.
6. Treat supplier decant words such as `отлив`, `отл`, `отливант`, `отлива`, `отливан`, and `отлив из флакона` as `parser_decant_term` knowledge. Decants are their own assistant category and should display `/ Decant` in the parsed identity while stripping the supplier decant marker from the scent name.
7. Treat supplier vintage words such as `винт`, `винтаж`, `vint`, and `vintage` as `parser_vintage_term` knowledge. Vintage rows are their own assistant category and should display `/ Vintage` in the parsed identity while stripping the supplier vintage marker from the scent name.
8. Treat supplier-only descriptor/color comments such as Cyrillic `белый` as `parser_supplier_comment_term` text that is stripped from the scent name without changing type or packaging.
9. Treat a supplier row name ending in a standalone `*` as a fake/garbage marker and exclude it from normal product parsing.
10. Distinguish refill products from refillable bottles: `refill`/`refil` means the product is a refill, while `refillable` is packaging/comment-style metadata and must not become part of the scent name or refill type.
11. Normalize Cyrillic/Latin lookalike letters inside otherwise Latin supplier tokens before parsing, so mixed-script scent names such as `Ciel` keep their Latin identity while fully Cyrillic supplier notes stay available to Russian term rules.
12. Treat short supplier audience markers such as `wom` as parser audience knowledge. Treat supplier `L` as Woman only when it appears as an isolated marker such as `(L)` or `[L]`, not when it is part of a scent word. When the catalogue has same-base men/women scents, keep the audience in the parsed scent name (`Guilty Woman`/`Guilty Man` or the catalogue's equivalent names).
13. Expand supplier abbreviations such as `Limited Ed.` to canonical name suffixes such as `Limited Edition`; when the supplier gives an audience hint and the catalogue has audience-specific base scents, keep that audience in the canonical scent name before the suffix.
14. Treat supplier collection-looking prefixes as untrusted unless backed by a brand-scoped collection alias; approved collection aliases may infer the brand when the supplier omitted it. Use product aliases when supplier text incorrectly prepends a collection name to a scent, preserve intentional uppercase suffixes such as `WB`, and canonicalize supplier-expanded short names such as `Saint Honore` to catalogue style `St Honore`. Generic words such as `collection` must not become generated brand aliases because they can falsely map unrelated supplier rows to brands like `Attar Collection`. Bare `attar` at the beginning of a supplier row is brand evidence for `Attar Collection`, not a concentration alias for `Perfume Oil`; explicit concentration text such as `edp` must remain authoritative.
15. Treat supplier rows containing Cyrillic `по мотивам` as inspired-by/imitation rows and route them to garbage/exclusion instead of matching them to the referenced real brand.
16. Build `MatchGroup` records for likely same-product rows.
17. Staff approve/reject/link through manual decisions or bulk actions.
18. Persist links to `SupplierProduct.catalog_perfume` and optional `catalog_variant`; audit decisions.

External catalogue import:
1. Parse saved HTML or parsed Fragrantica catalogue JSON/CSV into staged `FragranticaProduct` rows.
2. Dry-run first and show extracted collections/counts/sample rows.
3. Stage rows only after operator confirmation; collection names are resolved to brand-scoped `catalog.Collection` rows when the brand exists.
4. Review staged rows beside local catalogue rows. Suggested matches should be ranked and should use catalogue brand/product aliases, approved regex preprocess rules, loose punctuation keys, connector synonyms such as `&`/`and`/`et`, compact brand keys such as `Dolce&Gabbana`/`Dolce & Gabbana`, audience synonyms and suffixes such as `wom`, `man`/`men`, `woman`/`women`, `Pour Femme`, and `Pour Homme`, removable concentration phrases embedded in source scent titles such as `Light Blue Eau de Toilette`, and fuzzy same-brand scent scoring. Preserve audience-bearing scent suffixes while removing concentration words before falling back to looser audience cleanup. If a staged Fragrantica title explicitly names a concentration, treat matching concentration as exact evidence and cap contradictory concentration matches below exact confidence. Strip a repeated leading brand name from the staged Fragrantica scent identity for display, matching, and reviewed apply actions; for matching only, also try a source-name variant with a repeated leading source collection removed. Fragrantica product search must match all query tokens across staged fields together, including brand, normalized brand, collection, scent, normalized scent, audience, year, source path, URL, and domain. Reject opposite-audience candidates when both sides have known men/women audience. Apply product aliases after the same identity cleanup so alias rows still match when source titles include concentration or approved supplier abbreviations. Treat identity equality after this cleanup as an exact candidate; do not limit suggestions to exact raw normalized names.
5. Link staged rows to local `catalog.Perfume` records after review; keep local concentration and variants. Do not copy concentration words embedded in the Fragrantica scent title into the local scent name; use them only as match evidence. Linked rows are no longer suggestion candidates for the linked local product; review UI should show the existing linked counterpart only. Treat Fragrantica linking as one reviewed Fragrantica row to one Our Products perfume by default: when the same unlinked Fragrantica row is an equal top match for multiple local perfumes, remove it from bulk-ready linking and force manual review rather than auto-linking duplicates. When applying Fragrantica collection or all-uppercase scent names to a local product, normalize source casing to catalogue title case: major words capitalized, known short words such as `for`/`and` lowercased after the first word, and approved short acronyms such as `WB` preserved. If a brand has same-base men/women scents in local catalogue or staged Fragrantica rows, the reviewed local scent name must keep audience in the name while preserving the separate audience field: use `Pour Homme`/`Pour Femme` when the sibling set uses that style, otherwise use `for Men`/`for Women`.
6. Promote reviewed local Fragrantica links to live with the Fragrantica catalogue link export/import commands; do not copy the whole local database over live.
7. Create catalogue facts or aliases only from reviewed/approved work.

## Do not confuse

- Supplier product is not canonical product. It is a supplier offer row.
- Supplier inactivity is supplier-level visibility. Supplier-product browsers hide rows from inactive suppliers by default unless the inactive supplier filter is enabled; this is distinct from `SupplierProduct.is_active`.
- `prices.OurProduct` is not `catalog.Perfume`. `OurProduct` is legacy/internal grouping; `catalog.Perfume` is the canonical fragrance identity.
- Collection names are brand-scoped. Do not merge two collections only because their names match across different brands.
- `catalog.PerfumeVariant` is not a supplier row. It describes canonical size/package/type variants.
- Deleting selected rows from the Our Products products tab deletes catalogue variant rows only. It should not automatically delete the underlying `catalog.Perfume` identity.
- Deleting selected rows from the Our Products linking workbench deletes the selected `catalog.Perfume` identities and their variants, and clears Fragrantica matches back to unlinked.
- `FragranticaProduct` is not a supplier row or variant. It is staged external source evidence; it becomes trusted for local normalization only after staff links/applies it to `catalog.Perfume`.
- Alias/rule data is preferred over parser code for one-off brand, product, concentration, collection, or garbage corrections.
- Import batch/import file are operational audit records, not product identity.
- Price snapshots are history. `SupplierProduct.current_price` is the current offer state.
- OpenAI/mock suggestions are review inputs. They must not silently publish, overwrite, or bypass staff decisions.
