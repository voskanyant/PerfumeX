# Project Handoff

This document is the maintainer handoff for PerfumeX. It is written for the next developer or operator who needs to understand how the system is put together, how data moves through it, where the operational risks are, and what to touch carefully.

## 1. Project Summary

PerfumeX is a Django-based internal catalog and supplier-price ingestion system.

The application is responsible for:

- ingesting supplier price lists from files and email attachments
- parsing spreadsheets into normalized supplier products
- storing historical price snapshots
- linking multiple supplier offers to one internal product
- exposing a staff-only admin workspace for import operations
- exposing a logged-in viewer catalog for non-staff users

The codebase is small in repository breadth, but dense in one application. Most meaningful logic lives in the `prices` app.

## 2. Architecture At A Glance

### Runtime shape

- Framework: Django 5
- Templates: server-rendered Django templates
- Static serving: WhiteNoise
- Database: PostgreSQL only
- File storage: local media files on disk
- Background work: in-process Python threads from web requests, plus management commands
- Email ingest: IMAP
- Currency source: Central Bank of Russia XML endpoint

### App structure

- [perfumex/settings.py](perfumex/settings.py)
  Project settings, Postgres enforcement, static/media config, middleware.
- [perfumex/urls.py](perfumex/urls.py)
  Root routes for viewer, auth, custom admin, and Django admin.
- [prices/models.py](prices/models.py)
  Domain model.
- [prices/views.py](prices/views.py)
  Almost all page and JSON endpoint behavior.
- [prices/forms.py](prices/forms.py)
  UI forms and validation.
- [prices/services/importer.py](prices/services/importer.py)
  Spreadsheet parsing and product/snapshot upsert logic.
- [prices/services/email_importer.py](prices/services/email_importer.py)
  IMAP mailbox scanning, message matching, attachment dedupe, file creation.
- [prices/services/cbr_rates.py](prices/services/cbr_rates.py)
  Currency-rate fetching and persistence.

## 3. Routing Model

### Viewer routes

- `/`
  Product list for logged-in non-staff or staff users.
- `/products/search/`
  JSON live-search endpoint used by the viewer.
- `/products/<pk>/`
  Supplier product detail with price history.
- `/account/profile/`
  Profile and password change.

### Staff workspace routes

Mounted under `/admin/` and guarded by `AdminPanelStaffOnlyMiddleware`.

Main areas:

- dashboard
- documentation
- suppliers
- supplier overview and import logs
- supplier products
- our products
- product linking
- import settings
- currencies
- users and groups
- mailboxes

### Django admin

- `/django-admin/`

This is separate from the custom workspace and should be treated as secondary, not the primary operator UI.

## 4. Access Control And Middleware

### `ForceMoscowTimezoneMiddleware`

- Activates `Europe/Moscow` for every request.
- Most displayed dates and relative times should be read as Moscow-local values.

### `AdminPanelStaffOnlyMiddleware`

- Applies only to `/admin/`.
- Redirects anonymous users to login.
- Redirects authenticated non-staff users back to `/` for safe requests.
- Returns `403` for authenticated non-staff unsafe requests so POST bulk actions cannot silently redirect after denial.

### Staff-only management screens

User and group views use `StaffRequiredMixin` in addition to the `/admin/` middleware.

### Authentication And Access Rules

The custom `/admin/` workspace is staff-only at middleware level, but destructive POST actions also use explicit Django model permissions and return `403` when an authenticated user lacks the permission.

Permission gates for bulk or cleanup actions:

- `ImportDeleteBulkView`: `prices.delete_importbatch`
- `SupplierProductCleanupView`: `prices.delete_supplierproduct`
- `SupplierProductInactiveCleanupView`: `prices.delete_supplierproduct`
- `SupplierProductBulkDeleteView`: `prices.delete_supplierproduct`
- `SupplierEmailBackfillBulkView`: `prices.add_emailimportrun`
- `SupplierRatesRecalculateView`: `prices.change_exchangerate`
- `SupplierEmailImportAllView`: `prices.add_emailimportrun`
- `SupplierPriceReimportAllView`: `prices.change_importbatch`
- `CurrencyRateBulkDeleteView`: `prices.delete_exchangerate`

Single-object delete views inherit their delete permission from the target model through `BaseDeleteView`. The mapping preview upload endpoint relies on normal Django CSRF protection; keep `{% csrf_token %}` in the supplier import form and send `X-CSRFToken` from JavaScript previews.

Assistant linking routes use `StaffAssistantMixin`; POST handlers there require an authenticated staff user before any mutation runs.

## 5. Data Model And Responsibilities

### Supplier

Purpose:

- supplier identity
- default currency
- email matching patterns
- last-run diagnostics

Important fields:

- `name`
- `code`
- `default_currency`
- `from_address_pattern`
- `price_subject_pattern`
- `price_filename_pattern`
- `email_search_days`
- last email counters and messages

### Mailbox

Purpose:

- IMAP connection config
- processing priority
- UID cursors for incremental scanning

Important behavior:

- when a mailbox is re-enabled, UID cursors are reset in `save()`

### SupplierMailboxRule

Purpose:

- mailbox-specific routing rule for a supplier
- optional matching by sender, subject, and filename
- lets one mailbox route different suppliers differently

### SupplierFileMapping

Purpose:

- defines how to parse supplier spreadsheet files
- supports sheet names, sheet indexes, header row, and column map

Notes:

- mapping mode exists but current parsing logic mostly relies on stored numeric column values in `column_map`

### ImportBatch

Purpose:

- one import event for one supplier
- groups imported files from one email/manual action

Important fields:

- `supplier`
- `mailbox`
- `message_id`
- `received_at`
- `status`
- `error_message`

### ImportFile

Purpose:

- individual imported file
- holds file blob, mapping, content hash, processing status, and errors

Important behavior:

- actual upload path is built through `build_import_file_path()`
- paths are aggressively shortened to stay within legacy `varchar(100)` storage limits

### SupplierProduct

Purpose:

- current supplier catalog row
- one active/current representation per supplier identity key

Important fields:

- `our_product`
- `supplier`
- `supplier_sku`
- `identity_key`
- `name`
- `currency`
- `current_price`
- `current_stock`
- `last_imported_at`
- `last_import_batch`
- `created_import_batch`
- `is_active`

Important constraints:

- unique on `(supplier, identity_key)`

Interpretation:

- this table is the current state
- history lives in snapshots, not here

### OurProduct

Purpose:

- internal normalized product grouping
- one `OurProduct` can be linked from many `SupplierProduct` records

### PriceSnapshot

Purpose:

- append-only price history
- stores original price/currency plus derived USD/RUB values when available

Important:

- this is the main historical audit layer

### StockSnapshot

- stock history table
- much less central than `PriceSnapshot` in the current UI

### ExchangeRate

- dated conversion rates
- used for display and historical conversion

### EmailImportRun

- live progress and telemetry for email imports
- stores counters, status, last message, and detailed log

### ImportSettings

Singleton operational settings object.

Controls:

- import enabled flag
- run interval
- max messages per run
- supplier timeout
- auto mark seen
- product auto-deactivation window
- CBR markup
- filename blacklist

### UserPreference

- per-user saved filters and exclude terms
- currently used by viewer/search screens

## 6. End-To-End Price Import Flow

This is the most important flow in the system.

### Manual upload flow

1. User uploads a file through `ImportWizardView` or `SupplierImportView`.
2. `ImportBatch` is created.
3. `ImportFile` is created with `PENDING` status.
4. Active `SupplierFileMapping` is selected or updated.
5. `process_import_file(import_file)` is called.
6. Spreadsheet rows are parsed into normalized `ParsedRow` values.
7. Prices are converted into supplier default currency if needed.
8. Existing `SupplierProduct` rows are updated or new ones are created.
9. `PriceSnapshot` rows are appended.
10. If the imported batch is the latest for that supplier, missing products are marked inactive.
11. File and batch are marked `PROCESSED`, or `FAILED` with error text.

### Email import flow

1. `manage.py import_emails` decides whether it should run.
2. Stale pending import rows are auto-failed and optionally retried from stored files.
3. Daily CBR USD/RUB rate is synchronized if needed.
4. Active mailboxes are loaded by priority.
5. `run_import()` scans IMAP folders.
6. For Gmail, INBOX may be merged with All Mail.
7. Each message is decoded and attachments are inspected.
8. Each attachment is matched to a supplier via:
   - mailbox rules first
   - supplier fallback patterns second
9. Unsupported or blacklisted files are skipped.
10. Duplicate files are skipped via content hash + supplier + day window.
11. Matching attachments become `ImportBatch` + `ImportFile` records.
12. The same `process_import_file()` service parses them.
13. `EmailImportRun` is updated throughout the run.

## 7. Spreadsheet Parsing Rules

Parsing lives in [prices/services/importer.py](prices/services/importer.py).

### Supported formats

- `.csv`
- `.xlsx`
- `.xls`

### Important parsing behavior

- decimal parsing is heuristic and supports localized separators
- obvious mojibake repair is attempted for broken Cyrillic text
- SKU normalization strips `.0` style numeric artifacts
- name can be composed from multiple columns
- currency can come from:
  - explicit currency column
  - symbols inside the price cell
  - supplier default currency fallback
- parser skips short/invalid names and common total/shipping rows
- if configured header row fails to parse anything, the importer retries several candidate start rows
- importer raises if fewer than 100 unique rows are parsed

### Identity strategy

- if SKU exists, `identity_key` is normalized SKU
- otherwise, `identity_key` is normalized lowercase name

This is central to dedupe and product continuity.

## 8. Currency Logic

### Source

- CBR USD/RUB rate is fetched from `https://www.cbr.ru/scripts/XML_daily.asp`

### Stored behavior

- `upsert_cbr_markup_rates()` stores USD->RUB with configurable markup
- reverse RUB->USD CBR-markup rows are deleted, not stored permanently

### Usage

- imported supplier prices are normalized to supplier default currency when needed
- product lists can display converted prices in USD or RUB
- product detail history uses rates by snapshot date or nearest previous date

Key distinction:

- list display can use latest available rates
- history replay should use historical rates by snapshot date

Do not blur those two concepts in future changes.

## 9. Search, Filtering, And Linking

### Supplier product list

`SupplierProductListView` and `SupplierProductSearchView` power the admin product table and live search.

Current characteristics:

- page size 100
- filter by supplier, status, currency, exclude terms, price min/max
- search tokens support inline exclude terms
- sort by supplier, sku, name, price, last imported
- when showing all statuses, active rows are prioritized above inactive rows

### Viewer list

`ViewerProductListView` reuses supplier-product list logic with:

- different URLs
- no destructive actions
- saved front filters in `UserPreference`

### Product detail

`SupplierProductDetailView`:

- filters history by start/end date
- groups history to latest snapshot per local day
- builds chart series in original, USD, or RUB modes
- exposes link form for staff

### Product linking

`ProductLinkingSearchView`:

- tokenizes and normalizes text
- scores candidates with token overlap, brand match, and size match
- returns top internal product and supplier-product candidates

`ProductLinkingApplyView`:

- links a source supplier product to an existing `OurProduct`
- or creates a new `OurProduct` from another supplier product and links both

## 10. Import Operations UI

### Supplier overview

`SupplierOverviewView` is the operator control center.

It combines:

- suppliers with latest import run state
- latest import batches
- import log filters and sorting
- run status polling

### Import detailed logs

`ImportDetailedLogsView` focuses on detailed `EmailImportRun` telemetry and log tails.

### Import settings

`ImportSettingsView` handles:

- singleton settings edit
- scheduler install/remove
- run-now trigger

Important implementation detail:

- `run_now` starts a Python `threading.Thread` from the web request and calls `call_command("import_emails", force=True)`
- this is simple but not robust like a real job queue

## 11. Scheduler And Deployment

### Scheduler

The UI writes a cron entry marked with `PERFUMEX_IMPORT_CRON`.

Expected behavior:

- cron fires every 5 minutes
- actual command still respects `ImportSettings.interval_minutes`
- overlap protection is expected through the runner script/cron line generation in views

### Deployment

GitHub Actions workflow:

- file: [.github/workflows/deploy.yml](.github/workflows/deploy.yml)
- trigger: push to `main`
- action: SSH deploy

Server assumptions:

- app directory: `/opt/perfumex/PerfumeX`
- environment file: `/opt/perfumex/PerfumeX/.env`
- service name: `perfumex`
- web server: `nginx`

Deploy sequence:

1. pull latest main
2. install requirements
3. load `.env`
4. run migrations
5. collect static
6. restart app service
7. restart nginx

## 12. Local Development Notes

### Server startup

`run_python_server.cmd`:

- activates `.venv` if available
- sets local Postgres defaults
- runs `python manage.py runserver 127.0.0.1:8000 --noreload`

### Important consequence

Because of `--noreload`, changes often look "not applied" until the local server is restarted manually.

This has already caused confusion during UI work and should be remembered before debugging stale templates or CSS.

### Manual UI verification

Keyboard-test the mobile drawer focus trap after front-end changes:

```js
// Playwright snippet, run against a local dev server.
await page.goto("http://127.0.0.1:8000/admin/products/");
await page.getByRole("button", { name: /filters/i }).click();
const drawer = page.locator("[data-drawer='product-filters']");
await expect(drawer).toHaveClass(/is-open/);
await page.keyboard.press("Tab");
await expect(drawer.locator(":focus")).toHaveCount(1);
await page.keyboard.press("Shift+Tab");
await expect(drawer.locator(":focus")).toHaveCount(1);
await page.keyboard.press("Escape");
await expect(drawer).not.toHaveClass(/is-open/);
```

### Local database reality

- a local checkout may contain a large `db.sqlite3` artifact
- current Django settings do not allow SQLite
- local app still needs PostgreSQL

Treat `db.sqlite3` as an artifact, not the active runtime database.

### Removing sqlite from git history

If `db.sqlite3` ever appears in Git history, coordinate a short maintenance window before rewriting history because every collaborator and deployment checkout will need to rebase or reclone afterward. Do not run these commands casually on a shared branch.

Preferred `git filter-repo` flow:

1. Confirm the file is tracked or present in history:
   `git ls-files | grep sqlite3`
   `git log --all --oneline -- db.sqlite3`
2. Make a fresh mirror clone outside the working copy:
   `git clone --mirror <repo-url> perfumex-history-cleanup.git`
3. Rewrite history in that mirror:
   `git filter-repo --path db.sqlite3 --invert-paths`
4. Inspect the result:
   `git log --all --oneline -- db.sqlite3`
5. Force-push all rewritten refs only after operator approval:
   `git push --force --mirror`

BFG alternative:

1. Make a fresh mirror clone:
   `git clone --mirror <repo-url> perfumex-history-cleanup.git`
2. Remove sqlite blobs:
   `bfg --delete-files db.sqlite3`
3. Expire and compact old objects:
   `git reflog expire --expire=now --all`
   `git gc --prune=now --aggressive`
4. Force-push the cleaned mirror only after operator approval:
   `git push --force --mirror`

After either method, rotate any secrets that might have been present in the database and ask every clone to fetch the rewritten branch cleanly.

## 13. Management Commands

### `import_emails`

Primary scheduled import entry point.

Responsibilities:

- recover stale pending files
- retry interrupted imports from stored files
- sync daily CBR rate
- process either one supplier or all suppliers
- auto-deactivate stale products if configured

### `cleanup_duplicate_price_imports`

Find duplicate price imports using:

- supplier
- local day
- content hash

It can dry-run or actually delete duplicate batches.

### `import_supplier_folder`

Bulk-import local files from a server folder into one supplier.

Useful for historical backfills or migrations.

### `reorganize_import_files`

Moves stored import files into organized supplier folders and normalized names.

Useful after storage-path refactors.

### `repair_supplier_price_imports`

Reprocesses already stored processed price files to fix parsing mistakes.

Important behavior:

- deletes existing snapshots for the target batch/supplier before reparse
- can run by supplier or all suppliers

## 14. Operational Playbooks

### Onboard a new supplier

1. Create `Supplier`.
2. Set default currency.
3. Add sender email in `from_address_pattern`.
4. If mailbox-specific routing is needed, create `SupplierMailboxRule`.
5. Upload a sample file through supplier import.
6. Adjust sheet selector, header row, and columns until parsing succeeds.
7. Confirm products appear in supplier products list.
8. Optionally run email import for that supplier.

### Debug a failed import

1. Open supplier overview and inspect latest import batch/run.
2. Open import detail page for exact file and batch error.
3. Check whether the file had a mapping.
4. Check parsed header row and columns.
5. Confirm attachment was not skipped by filename blacklist.
6. Confirm sender/subject/filename matching patterns.
7. If the file exists in storage and parse logic changed, use `repair_supplier_price_imports`.

### Investigate missing email imports

1. Verify mailbox is active.
2. Verify supplier has sender email configured.
3. Check `EmailImportRun` status and detailed log.
4. Check mailbox host and credentials.
5. Confirm attachment extension is supported.
6. Confirm file was not deduped by same-day hash logic.
7. Confirm the message was inside the search date window.

### Recalculate rates

Use the admin action that calls `SupplierRatesRecalculateView`, or sync dates directly with the CBR service logic if you are scripting.

### Clean bad duplicate imports

Use `cleanup_duplicate_price_imports` in dry-run first.

### Rebuild corrupted historical prices

Use `repair_supplier_price_imports`.

## 15. Known Risks And Technical Debt

These are the main things the next maintainer should know before making big changes.

### 1. Single-app density

Almost everything is in `prices`.

Impact:

- low discoverability
- large `views.py`
- high coupling between UI and domain rules

### 2. Background work in web threads

Several UI actions spawn threads in-process.

Impact:

- fragile under process restarts
- harder to observe than a queue worker
- easy to lose progress on deploy or crash

### 3. Minimal tests

[prices/tests.py](prices/tests.py) is effectively empty.

Impact:

- no automated safety net for import parsing, search behavior, or linking rules

### 4. Historical parsing assumptions

Importer contains multiple heuristics:

- decimal separator normalization
- mojibake repair
- fallback header row retries
- minimum parsed row count

Impact:

- changes here can fix one supplier and break another

### 5. Storage-path legacy limits

Import file path builder still optimizes around short path lengths due to legacy database field constraints.

### 6. Stale local server confusion

`run_python_server.cmd` uses `--noreload`.

Impact:

- developers can misread stale UI as bad code changes

## 16. Safe Change Rules

If you change these areas, verify more than once:

### Importer changes

- test with real supplier files from different sources
- verify price count does not drop unexpectedly
- verify inactive-product logic still behaves correctly

### Search/list changes

- verify desktop and mobile
- verify saved filters still restore correctly
- verify live search endpoint matches server-rendered list behavior

### Currency changes

- keep latest-rate display separate from historical replay
- verify both list and detail pages

### Scheduler changes

- preserve no-overlap behavior
- preserve force-run behavior
- verify `last_run_at` semantics

## 17. Recommended Next Improvements

If there is time for infrastructure work, these are the highest-value upgrades:

1. Split domain logic out of `views.py`.
2. Move background imports to a proper job runner.
3. Add importer regression tests with fixture files.
4. Add smoke tests for search and linking.
5. Separate public viewer concerns from staff workspace concerns more clearly.
6. Document production secrets and cron bootstrap more explicitly outside the codebase if not already done privately.

## 18. Quick File Map For New Maintainers

- [README.md](README.md)
- [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)
- [perfumex/settings.py](perfumex/settings.py)
- [perfumex/urls.py](perfumex/urls.py)
- [prices/models.py](prices/models.py)
- [prices/views.py](prices/views.py)
- [prices/forms.py](prices/forms.py)
- [prices/services/importer.py](prices/services/importer.py)
- [prices/services/email_importer.py](prices/services/email_importer.py)
- [prices/services/cbr_rates.py](prices/services/cbr_rates.py)
- [prices/templates/prices/documentation.html](prices/templates/prices/documentation.html)
- [.github/workflows/deploy.yml](.github/workflows/deploy.yml)

## 19. Final Notes

The two most important truths about this codebase are:

1. the import pipeline is the product
2. most operational bugs will come from mismatched supplier files, mailbox matching, or stale assumptions around parsing and background execution

Treat importer changes, dedupe changes, and historical-rate changes as high-risk even if the code edit looks small.
