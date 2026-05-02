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
- Price snapshot - historical price observation for a supplier product. Model: `prices.PriceSnapshot`.
- Stock snapshot - historical stock observation for a supplier product. Model: `prices.StockSnapshot`.
- Internal product / Our Product - older internal grouping entity used by the price workspace. Model: `prices.OurProduct`.
- Catalogue brand - canonical perfume brand. Model: `catalog.Brand`.
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
- Normalization stats snapshot - cached counts for parser queues and issue categories. Model: `assistant_linking.NormalizationStatsSnapshot`.
- Match group - grouped parsed products that appear to refer to the same catalogue item/variant. Model: `assistant_linking.MatchGroup`.
- Match group item - supplier product membership in a match group. Model: `assistant_linking.MatchGroupItem`.
- Manual link decision - staff approval/rejection/link decision for a supplier row. Model: `assistant_linking.ManualLinkDecision`.
- Manual link decision audit - record of replaced manual decisions. Model: `assistant_linking.ManualLinkDecisionAudit`.
- Link action - undoable bulk/link operation payload. Model: `assistant_linking.LinkAction`.
- Link suggestion - candidate link from deterministic/mock/OpenAI suggestion engines. Model: `assistant_linking.LinkSuggestion`.
- Fragrantica product - staged external catalogue row from saved HTML or parsed catalogue JSON/CSV import; source evidence that can be linked to `catalog.Perfume` after staff review. Brand/name/collection/audience/year/source URL may update or support the local catalogue through that reviewed link; concentration and variants remain local catalogue data. Model: `assistant_linking.FragranticaProduct`.
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
2. Apply catalogue facts, aliases, concentration aliases, parser rules, and garbage terms.
3. Surface low-confidence/missing/conflict rows in normalization queues.
4. Build `MatchGroup` records for likely same-product rows.
5. Staff approve/reject/link through manual decisions or bulk actions.
6. Persist links to `SupplierProduct.catalog_perfume` and optional `catalog_variant`; audit decisions.

External catalogue import:
1. Parse saved HTML or parsed Fragrantica catalogue JSON/CSV into staged `FragranticaProduct` rows.
2. Dry-run first and show extracted collections/counts/sample rows.
3. Stage rows only after operator confirmation.
4. Review staged rows beside local catalogue rows.
5. Link staged rows to local `catalog.Perfume` records after review; keep local concentration and variants.
6. Promote reviewed local Fragrantica links to live with the Fragrantica catalogue link export/import commands; do not copy the whole local database over live.
7. Create catalogue facts or aliases only from reviewed/approved work.

## Do not confuse

- Supplier product is not canonical product. It is a supplier offer row.
- `prices.OurProduct` is not `catalog.Perfume`. `OurProduct` is legacy/internal grouping; `catalog.Perfume` is the canonical fragrance identity.
- `catalog.PerfumeVariant` is not a supplier row. It describes canonical size/package/type variants.
- `FragranticaProduct` is not a supplier row or variant. It is staged external source evidence; it becomes trusted for local normalization only after staff links/applies it to `catalog.Perfume`.
- Alias/rule data is preferred over parser code for one-off brand, product, concentration, collection, or garbage corrections.
- Import batch/import file are operational audit records, not product identity.
- Price snapshots are history. `SupplierProduct.current_price` is the current offer state.
- OpenAI/mock suggestions are review inputs. They must not silently publish, overwrite, or bypass staff decisions.
