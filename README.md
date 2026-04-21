# PerfumeX

PerfumeX is a Django 5 application for ingesting supplier price lists, normalizing supplier catalogs, tracking historical prices, linking supplier products to internal products, and exposing both a staff-facing admin workspace and a login-protected viewer catalog.

This repository currently uses one main Django app, `prices`, for nearly all domain logic.

## Stack

- Python + Django 5
- PostgreSQL only
- Server-rendered Django templates
- WhiteNoise for static files
- `openpyxl` and `xlrd` for spreadsheet parsing
- IMAP-based email import for supplier attachments

## What The App Does

The system handles five main jobs:

1. Maintain supplier master data, mailbox rules, and file mappings.
2. Import supplier price files manually or from email attachments.
3. Normalize imported rows into `SupplierProduct` records and store price history in `PriceSnapshot`.
4. Link supplier products to `OurProduct` records for internal catalog grouping.
5. Provide search, filtering, history, and audit screens for operators and regular logged-in users.

## Main Surfaces

### Public / viewer-facing

- `/` - viewer product list
- `/products/search/` - viewer live search JSON endpoint
- `/products/<id>/` - viewer product detail
- `/account/profile/` - logged-in user profile

Note: the viewer is not anonymous. It still requires authentication through `LoginRequiredMixin`.

### Staff admin workspace

- `/admin/` - dashboard
- `/admin/docs/` - in-app technical documentation
- `/admin/suppliers/` - suppliers
- `/admin/suppliers/overview/` - import operations overview
- `/admin/products/` - supplier products
- `/admin/our-products/` - internal products
- `/admin/linking/` - linking workspace
- `/admin/settings/*` - currencies, import settings, users, groups, mailboxes

### Django admin

- `/django-admin/`

This is separate from the custom staff workspace under `/admin/`.

## Authentication And Access Rules

- All major views require login.
- Custom admin routes under `/admin/` are blocked for non-staff users by `prices.middleware.AdminPanelStaffOnlyMiddleware`.
- Request timezone is forced to `Europe/Moscow` by `prices.middleware.ForceMoscowTimezoneMiddleware`.

## Database

This project is configured for PostgreSQL only.

`perfumex/settings.py` explicitly raises an error if `DATABASE_ENGINE` is not `postgres`.

Important:

- `db.sqlite3` exists in the repository/worktree, but the app is not configured to use SQLite.
- Local development must still point at PostgreSQL.

## Local Setup

### Requirements

- Python 3.13-compatible environment
- PostgreSQL running locally
- Optional virtual environment at `.venv`

### Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Required environment

The application expects these variables:

- `DATABASE_ENGINE=postgres`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`

### Local run shortcut

`run_python_server.cmd`:

- activates `.venv` if present
- sets PostgreSQL defaults for local development
- runs `python manage.py runserver 127.0.0.1:8000 --noreload`

The `--noreload` part matters: template and Python edits do not auto-reload. Restart the server after changes.

### Common local commands

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
python manage.py import_emails --force
```

## High-Level Data Model

Core models:

- `Supplier` - supplier identity, email patterns, diagnostics
- `Mailbox` - IMAP connection settings and UID cursors
- `SupplierMailboxRule` - mailbox-specific matching overrides
- `SupplierFileMapping` - spreadsheet parsing configuration
- `ImportBatch` - one import event for one supplier
- `ImportFile` - one imported file/attachment plus parse status and hash
- `SupplierProduct` - current supplier catalog record
- `PriceSnapshot` - historical price record
- `StockSnapshot` - stock history record
- `OurProduct` - internal catalog entity
- `ExchangeRate` - dated conversion rates
- `ImportSettings` - singleton operational settings
- `EmailImportRun` - run progress and detailed log
- `UserPreference` - per-user saved filters and exclude terms

The most important audit chain is:

`SupplierProduct <- PriceSnapshot <- ImportBatch <- ImportFile`

## Import Flows

### Manual file import

- Upload file in the admin UI.
- Create `ImportBatch` and `ImportFile`.
- Find active supplier mapping.
- Parse the file.
- Upsert `SupplierProduct` rows.
- Create `PriceSnapshot` rows.
- Mark untouched products inactive if the file is the latest batch for that supplier.

### Email import

- `manage.py import_emails` scans active IMAP mailboxes.
- Supplier matching is done through mailbox rules first, then supplier fallback patterns.
- Attachments are deduplicated by supplier + local day window + SHA-256 content hash.
- Matching price files are saved as `ImportFile` blobs and parsed through the same importer service as manual uploads.

## Other Management Commands

- `python manage.py import_emails`
- `python manage.py cleanup_duplicate_price_imports`
- `python manage.py import_supplier_folder`
- `python manage.py reorganize_import_files`
- `python manage.py repair_supplier_price_imports`

See [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md) for what each command does and when to use it.

## Deployment

Deployment is defined in `.github/workflows/deploy.yml`.

Current behavior on push to `main`:

1. SSH into the server.
2. `git pull origin main`
3. `pip install -r requirements.txt`
4. load `.env`
5. `python manage.py migrate`
6. `python manage.py collectstatic --noinput`
7. restart `perfumex`
8. restart `nginx`

Server paths assumed by the workflow:

- app root: `/opt/perfumex/PerfumeX`
- venv: `/opt/perfumex/PerfumeX/.venv`
- env file: `/opt/perfumex/PerfumeX/.env`

## Repository Layout

```text
perfumex/   Django project settings and root URLs
prices/     Main application: models, views, services, forms, templates, static
scripts/    Utility scripts
media/      Uploaded import files
.github/    Deployment workflow
```

## Current Constraints

- Almost all business logic lives in one Django app: `prices`.
- Background UI actions use Python threads from the web process instead of a job queue.
- Automated test coverage is effectively absent; `prices/tests.py` is only a stub.
- Local run helper uses `--noreload`, so stale processes can make changes appear missing until restart.

## Recommended Starting Points

- Read [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md) first.
- Then inspect:
  - [perfumex/settings.py](perfumex/settings.py)
  - [perfumex/urls.py](perfumex/urls.py)
  - [prices/models.py](prices/models.py)
  - [prices/views.py](prices/views.py)
  - [prices/services/importer.py](prices/services/importer.py)
  - [prices/services/email_importer.py](prices/services/email_importer.py)
