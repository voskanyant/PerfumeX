# HTML Catalogue Import Rules

This importer follows `assistant_linking/docs/assistant_learning_design.md`: saved catalogue HTML is converted into catalogue facts and aliases, not hardcoded parser exceptions.

Use `python manage.py import_brand_catalog_html <path>` for saved brand catalogue pages.

Current parser rule:

- A `h2.tw-gridlist-section-title` starts a collection section.
- Every following `a.prefumeHbox` fragrance row belongs to that collection until the next section title.
- `All Fragrances` is an index section, not a real collection. It is used only when no more specific section exists.
- Fragrance name comes from `h3.tw-perfume-title`.
- Brand name comes from `p.tw-perfume-designer`.
- Release year comes from `span.tw-year-badge`.
- Audience comes from the fragrance row class first: `tw-listview-item-female` -> `Women`, `tw-listview-item-male` -> `Men`, `tw-listview-item-unisex` -> `Unisex`. If those classes are missing, use the row `aria-label`/`title` text as a fallback for `женский`, `мужской`, `унисекс`, `female`, `male`, and `unisex`.

Default command mode is dry-run. Use it first to compare source catalogue rows with local `catalog.Perfume` rows and generate a missing CSV.

Operator preview rule:

- Always run a dry-run first.
- Show the operator the extracted brand, collection list, item count, sample rows, and missing/matched report before using `--apply`.
- Do not push code or tell the operator the extraction is complete until the operator has seen the dry-run result and confirmed the apply step.
- Keep the dry-run report path in the handoff or final response so the operator can inspect what will be imported.

Write behavior:

- `--apply` updates matched local `catalog.Perfume.collection_name`, `audience`, and `release_year`.
- `--create-aliases` creates brand/product aliases so supplier normalization can learn the collection.
- `--create-missing-catalog` creates missing `catalog.Perfume` rows as review-status catalogue entries.
- `--reparse-supplier-products` reparses supplier products whose names contain the imported brand name after aliases are written, because new aliases can change rows that are not otherwise stale.
- `--reparse-all-supplier-products` refreshes the full supplier catalogue and should be reserved for intentional full rebuilds.
