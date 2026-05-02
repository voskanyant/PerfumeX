# HTML Catalogue Import Rules

This importer follows `assistant_linking/docs/assistant_learning_design.md`: saved catalogue HTML or parsed Fragrantica catalogue JSON/CSV is converted into staged external catalogue rows, not hardcoded parser exceptions and not immediate edits to Our Products.

Use `python manage.py import_brand_catalog_html <path>` for saved brand catalogue pages.
Use `python manage.py import_brand_catalog_folder <folder> --pattern "*.json"` for parsed Fragrantica catalogue export folders.

Current parser rule:

- A `h2.tw-gridlist-section-title` starts a collection section.
- Every following `a.prefumeHbox` fragrance row belongs to that collection until the next section title.
- `All Fragrances` is an index section, not a real collection. It is used only when no more specific section exists.
- Fragrance name comes from `h3.tw-perfume-title`.
- Brand name comes from `p.tw-perfume-designer`.
- Release year comes from `span.tw-year-badge`.
- Parsed JSON/CSV rows use `fragrance_name`, `brand`/`designer`, `collection` or non-`All Fragrances` `section`, `year`, `gender`, and `url`.
- Parsed `gender` values are normalized to display audience labels: `female` -> `Women`, `male` -> `Men`, and `unisex` -> `Unisex`.
- Parsed `url` is stored as the row source link and should remain visible in the Fragrantica staging review UI.
- Audience comes from the fragrance row class first: `tw-listview-item-female` -> `Women`, `tw-listview-item-male` -> `Men`, `tw-listview-item-unisex` -> `Unisex`. If those classes are missing, use the row `aria-label`/`title` text as a fallback for `женский`, `мужской`, `унисекс`, `female`, `male`, and `unisex`.

Default command mode is dry-run. Use it first to inspect source catalogue rows before staging them in `assistant_linking.FragranticaProduct`.

Operator preview rule:

- Always run a dry-run first.
- Show the operator the extracted brand, collection list, item count, sample rows, and new/existing staged-row report before using `--apply`.
- Do not push code or tell the operator the extraction is complete until the operator has seen the dry-run result and confirmed the apply step.
- Keep the dry-run report path in the handoff or final response so the operator can inspect what will be staged.

Write behavior:

- `--apply` creates or updates `assistant_linking.FragranticaProduct` rows only.
- Fragrantica source import must not create/update `catalog.Perfume`, `BrandAlias`, or `ProductAlias` rows directly.
- `--create-aliases` and `--create-missing-catalog` are disabled because links and merges must be reviewed first.
- `--reparse-supplier-products` should be used only after approved aliases or catalogue changes are created from the review/linking interface.
- `--reparse-all-supplier-products` refreshes the full supplier catalogue and should be reserved for intentional full rebuilds after approved knowledge changes.
