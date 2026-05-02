# Contributing

## Purpose of this document

Short checklist for local contribution checks. For setup and operations, use [README.md](README.md). For AI-agent work, use [AGENTS.md](AGENTS.md), [docs/WORKING_RULES.md](docs/WORKING_RULES.md), and [docs/DRIFT_CHECKLIST.md](docs/DRIFT_CHECKLIST.md).

Before pushing to `main` or triggering deploy, run the GitHub-equivalent deploy gate:

```bash
pip install -r requirements-dev.txt
npm install
make deploy-gate
```

`make deploy-gate` mirrors the GitHub CI jobs required before deploy. It expects PostgreSQL to be reachable through the `POSTGRES_*` environment variables. The defaults match a local database on `127.0.0.1:5432`; override them when needed.

For normal branch work that is not deploying, run:

```bash
make ci
```

For a faster local guard while iterating, run:

```bash
make local-smoke
```

`make local-smoke` runs JavaScript syntax checks in all checkouts and runs `npm run lint:js` too when `node_modules` is installed.

When editing the local smoke runner, run `make local-smoke-rules`.

When editing Python files that may not be imported by Django checks, run `make python-smoke`.

When editing the Python syntax checker, run `make python-rules`.

When editing migrations or migration dependencies, run `make migration-graph-smoke`.

When editing the migration graph checker, run `make migration-graph-rules`.

When editing static JavaScript and `npm run lint:js` is unavailable locally, run `make js-smoke`.

When editing the JavaScript syntax checker, run `make js-rules`.

When editing `AGENTS.md` or focused repo-memory docs, run `make agent-docs-smoke`.

When editing the agent-doc checker, run `make agent-docs-rules`.

When editing code, UI, or docs where documentation companions may need review, run `make doc-drift`.

When editing the doc-drift checker, run `make doc-drift-rules`.

When editing Markdown documentation links, run `make markdown-link-smoke`.

When editing the Markdown link checker, run `make markdown-link-rules`.

When editing the Makefile smoke/check target surface, run `make make-target-smoke`.

When editing the Makefile target checker, run `make make-target-rules`.

When editing JavaScript or inline template scripts that render backend data, run `make js-dom-safety`.

When editing the JavaScript DOM safety checker, run `make js-dom-safety-rules`.

When editing JavaScript that generates checkbox or radio controls, run `make js-a11y`.

When editing the JavaScript accessibility checker, run `make js-a11y-rules`.

When editing JavaScript that generates table rows or data cells, run `make js-table-labels`.

When editing the JavaScript table-label checker, run `make js-table-labels-rules`.

Before sharing a branch, run `make secret-smoke` to catch obvious committed credentials.

When editing the secret-pattern checker, run `make secret-rules`.

When editing service modules or extracting logic into services, run `make service-smoke`.

When editing the service import checker, run `make service-rules`.

When editing management commands or command-facing services, run `make command-smoke`.

When editing the management-command checker, run `make command-rules`.

When editing Django templates, run `make template-smoke`.

When editing the template compile checker, run `make template-rules`.

When editing full-page templates or template shell/layout conventions, run `make template-layout-smoke`.

When editing the template layout checker, run `make template-layout-rules`.

When editing URL names or template `{% url %}` references, run `make template-url-smoke`.

When editing the template URL checker, run `make template-url-rules`.

When editing root/app URL configuration, run `make url-smoke`.

When editing the URL configuration checker, run `make url-rules`.

When editing template static asset references, run `make static-ref-smoke`.

When editing the static-reference checker, run `make static-ref-rules`.

When editing icon-only actions or image tags in templates, run `make template-a11y-smoke`.

When editing the template accessibility checker, run `make template-a11y-rules`.

When editing template buttons, run `make template-button-smoke`.

When editing the template button checker, run `make template-button-rules`.

When editing template ids or JavaScript hooks that reference controls, run `make template-id-smoke`.

When editing the template id checker, run `make template-id-rules`.

When editing static CSS, run `make css-smoke`.

When editing the CSS rule checker, run `make css-rules`.

When editing responsive table markup, run `make table-mobile-smoke`.

When editing the mobile table checker, run `make table-mobile-rules`.

When editing table headers in templates, run `make table-header-smoke`.

When editing the table-header checker, run `make table-header-rules`.

When editing POST forms in templates, run `make template-csrf-smoke`.

When editing the template CSRF checker, run `make template-csrf-rules`.

When editing drawers or native dialogs in templates, run `make template-drawer-smoke`.

When editing the template drawer/dialog checker, run `make template-drawer-rules`.

When editing template visual styling, run `make template-inline-style-smoke`.

When editing the template inline-style checker, run `make template-inline-style-rules`.

When editing labels or form control ids in templates, run `make template-label-smoke`.

When editing the template label checker, run `make template-label-rules`.

When editing links in templates, run `make template-link-smoke`.

When editing the template link checker, run `make template-link-rules`.

When editing destructive POST actions in templates, run `make destructive-action-smoke`.

When editing the destructive-action checker, run `make destructive-action-rules`.

When editing repository check scripts directly, run the matching focused check, such as `make doc-drift-rules`, `make view-export-smoke`, or `make ui-partial-smoke`.

When editing the view export checker wrapper, run `make view-export-rules`.

When editing the UI partial checker wrapper, run `make ui-partial-rules`.
