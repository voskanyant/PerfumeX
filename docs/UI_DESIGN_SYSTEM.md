# UI Design System

## Purpose of this document

Current UI rules for Django templates, CSS, and UI JavaScript. Use this before changing templates, static assets, layout, visual patterns, responsive behavior, tabs, tables, filters, forms, pagination, buttons, or messages.

Related docs: [AGENTS.md](../AGENTS.md), [README.md](../README.md), [docs/REPO_MAP.md](REPO_MAP.md), [docs/WORKING_RULES.md](WORKING_RULES.md), [docs/CODEX_TASKS.md](CODEX_TASKS.md), [docs/DECISIONS.md](DECISIONS.md), [docs/DRIFT_CHECKLIST.md](DRIFT_CHECKLIST.md).

Do not redesign the UI when adding a feature.

## Before adding UI, Codex must search for examples

First find an existing similar component and copy its structure/classes before inventing anything new.

Use these searches:

```bash
rg -n "page-header|page_head|page-actions|page-title" prices/templates assistant_core/templates assistant_linking/templates
rg -n "pagination|pagination-shell|workspace-pagination|page-link" prices/templates assistant_core/templates assistant_linking/templates
rg -n "nav-tabs|tabs|class=\"tab|_tab_items" prices/templates assistant_core/templates assistant_linking/templates
rg -n "<table|data-table|table-mobile|table-wrap|table_empty" prices/templates assistant_core/templates assistant_linking/templates
rg -n "button|btn|data-confirm|button danger|button ghost|button primary" prices/templates assistant_core/templates assistant_linking/templates
rg -n "<form|form-shell|form-label|form-actions|form-field" prices/templates assistant_core/templates assistant_linking/templates
rg -n "filter|search-wrap|search-strip|search-inline-row|drawer" prices/templates assistant_core/templates assistant_linking/templates
rg -n "card|section-card|section-panel|metric-card|workspace-block" prices/templates assistant_core/templates assistant_linking/templates
```

If `rg` is unavailable, use PowerShell:

```powershell
Get-ChildItem -Recurse -File -Include *.html -Path prices\templates,assistant_core\templates,assistant_linking\templates | Select-String -Pattern "pagination"
Get-ChildItem -Recurse -File -Include *.html -Path prices\templates,assistant_core\templates,assistant_linking\templates | Select-String -Pattern "tabs"
Get-ChildItem -Recurse -File -Include *.html -Path prices\templates,assistant_core\templates,assistant_linking\templates | Select-String -Pattern "table"
Get-ChildItem -Recurse -File -Include *.html -Path prices\templates,assistant_core\templates,assistant_linking\templates | Select-String -Pattern "btn|button"
Get-ChildItem -Recurse -File -Include *.html -Path prices\templates,assistant_core\templates,assistant_linking\templates | Select-String -Pattern "form"
Get-ChildItem -Recurse -File -Include *.html -Path prices\templates,assistant_core\templates,assistant_linking\templates | Select-String -Pattern "filter"
Get-ChildItem -Recurse -File -Include *.html -Path prices\templates,assistant_core\templates,assistant_linking\templates | Select-String -Pattern "card"
Get-ChildItem -Recurse -File -Include *.html -Path prices\templates,assistant_core\templates,assistant_linking\templates | Select-String -Pattern "page-header"
```

If no pattern exists, create the smallest reusable pattern, place it near the owning app or shared `prices/templates/includes/` when it is cross-app, and document it here.

## Reusable template partials

Prefer these shared include paths for new templates:

```django
{% include "includes/page_header.html" with kicker="Area" title="Page title" meta="Short context." primary_url=create_url primary_label="Add item" %}
{% include "includes/page_header.html" with kicker="Area" title="Page title" subtitle="Short page explanation." subtitle_class="space-bottom-none" %}
{% include "includes/page_header.html" with kicker="Delete" title_prefix="Delete " title=object subtitle="This action cannot be undone." %}
{% include "includes/page_header.html" with kicker="Area" title="Page title" meta_template="app/path/_header_meta.html" actions_template="app/path/_header_actions.html" %}
{% include "includes/tabs.html" with label="Sections" items_template="prices/_import_tab_items.html" %}
{% include "includes/pagination.html" with page_obj=page_obj page_param="page" label="Pagination" jump_id="page-jump" %}
{% include "includes/pagination.html" with page_obj=page_obj html_id="pagination-controls" class_name="workspace-pagination" %}
{% include "includes/table_empty.html" with colspan=5 message="No records found." %}
{% include "includes/empty_state.html" with message="No records found." %}
{% include "includes/messages.html" %}
```

- Shared partials live in `prices/templates/includes/` because Django uses app template discovery and no root template directory.
- `includes/page_header.html`, `includes/tabs.html`, `includes/pagination.html`, and `includes/table_empty.html` delegate to the existing `prices/components/` implementations, so they preserve the current visual style.
- Use `meta_template` only when the header needs conditional status, subtitle, or composed metadata markup; keep the partial short and presentation-only.
- Use `includes/messages.html` only from base/layout templates; normal pages should rely on `prices/base.html`.
- Keep specialized legacy paginators and tabs in place unless the page is already being touched and the replacement is obvious.
- After changing shared include/component markup, run `python scripts/check_ui_partials.py`.

## App shell and layout

- All main pages extend `prices/base.html`.
- Full-page templates must start their content with the shared `includes/page_header.html`, a breadcrumbs/page-header pattern, or a documented exception such as `products-page-header`, `supplier-import-hero`, or `login-shell`.
- Run `python scripts/check_template_layout.py` after adding or moving full-page templates.
- Base CSS comes from `prices/css/app.css` and `prices/css/pages.css`; page-specific CSS is loaded in `extra_head` (`imports.css`, `products.css`, `detail.css`).
- Authenticated pages use `.layout`, `.sidebar`, `.topbar`, `.mobile-topbar`, and `.content`.
- Page content should use `.page-stack` as the outer wrapper.
- Use `.layout-grid-12`, `.page-grid-3`, `.layout-row`, `.layout-inline`, `.flex-wrap`, `.gap-sm`, `.gap-md`, `.items-end`, and `.is-full-width` instead of one-off layout CSS.
- Do not add a new shell, sidebar, topbar, background, or typography scheme.

## Page titles and actions

Preferred structure:

```django
{% include "includes/page_header.html" with kicker="Area" title="Page title" meta="Short context." primary_url=create_url primary_label="Add item" %}
{% include "includes/page_header.html" with kicker="Area" title="Page title" subtitle="Short page explanation." subtitle_class="space-bottom-none" %}
{% include "includes/page_header.html" with kicker="Delete" title_prefix="Delete " title=object subtitle="This action cannot be undone." %}
{% include "includes/page_header.html" with kicker="Area" title="Page title" meta_template="app/path/_header_meta.html" actions_template="app/path/_header_actions.html" %}
```

Manual equivalent when needed:

```html
<div class="page-header">
  <div>
    <span class="page-kicker">Area</span>
    <h1 class="page-title">Page title</h1>
    <div class="page-meta">Short context.</div>
  </div>
  <div class="page-actions">...</div>
</div>
```

- Keep actions in `.page-actions` on the right of `.page-header`/`.page-head`.
- Use `subtitle` for normal explanatory copy and `meta` for compact status/count metadata.
- Use `title_prefix` only for simple generated titles such as delete-confirmation pages.
- On mobile, actions wrap; do not force fixed widths that overflow.
- Use page headers consistently before filters, tabs, tables, or work panels.
- `python scripts/check_template_layout.py` enforces the shared base and top-level page-header pattern for full-page templates.

Specialized exceptions:
- Product list headers in `prices/list.html` use `products-page-header` and `generic-list-header` because the product browser has live search, drawers, and bulk-action state.
- Supplier import uses `supplier-import-hero` because the source tabs and supplier route metadata are tied to that page layout.
- Section headers inside cards, summaries, chart panels, and queue shortcut panels may use local heading markup when they are not the page's top header.
- Login may keep its compact auth-card header inside `login-shell`.

## Tabs

Standard structure:

```django
{% include "includes/tabs.html" with class_name="import-tabs" label="Import sections" items_template="prices/_import_tab_items.html" %}
```

Tab item structure:

```html
<a class="tab {% if active %}active{% endif %}" href="...">Label</a>
```

- Tabs use `.tabs` plus `.tab`; active state is `.active` or `.is-active`.
- Existing examples: `prices/_import_tabs.html`, `prices/_import_tab_items.html`, `assistant_core/catalog/_nav.html`, `prices/our_products_catalog.html`, `prices/supplier_import.html`.
- Do not introduce `nav-tabs` or a new tab visual language.
- Tabs scroll horizontally on mobile; preserve that behavior.

## Pagination

Preferred structure:

```django
{% include "includes/pagination.html" with page_obj=page_obj page_param="page" label="Pagination" jump_id="page-jump" %}
```

Standard classes:
- `.pagination-shell`
- `.pagination-list`
- `.page-link`
- `.page-link.is-active`
- `.page-link.is-disabled` for non-interactive gap markers
- `.pagination-jump`
- `.pagination-summary`

- Use the shared pagination component for new screens.
- Preserve current query parameters in pagination forms/links.
- Shared pagination must use an elided page range for large datasets; do not loop over every page number in templates and hide most of them with conditionals.
- If JavaScript needs a stable pagination container, pass `html_id` to the shared include instead of hand-writing pagination markup.
- If JavaScript updates pagination in place, render the standard children (`.pagination-list`, `.page-link`, `.page-link.is-active`, `.pagination-summary`) inside the shared `.pagination-shell` container; do not append a second nested `<nav>`.
- Existing manual/specialized paginators should be migrated only when the page is already being touched and the replacement is obvious.
- Avoid plain `.pagination` with only Previous/Next for new work.

## Tables

Standard responsive structure:

```html
<div class="table-wrap table-scroll-x">
  <table class="data-table table-mobile">
    <thead>...</thead>
    <tbody>
      <tr>
        <td data-label="Supplier">...</td>
        <td data-label="Actions" class="actions">...</td>
      </tr>
    </tbody>
  </table>
</div>
```

- Use `.data-table table-mobile` for new data tables.
- Wrap wide tables in `.table-wrap table-scroll-x`; use `.table-scroll-md` or `.table-scroll-lg` only for intentionally capped-height work areas.
- Every mobile table cell needs `data-label`, except table-empty rows.
- JavaScript-generated table cells should set `data-label`; empty-state or divider cells should set `colSpan`.
- Every table header cell should declare `scope="col"` so screen readers and static checks can map columns correctly.
- Empty, icon-only, checkbox-only, or action-only table headers need an accessible name such as `aria-label="Actions"` or `aria-label="Select"`.
- Standalone checkbox/radio controls need a programmatic label: `aria-label`, `aria-labelledby`, `title`, a matching `<label for="...">`, or a wrapping `<label>`.
- JavaScript-generated checkbox/radio controls must follow the same label rule; prefer setting `aria-label` before appending the control unless it is appended inside a generated `<label>`.
- Run `python scripts/check_table_mobile.py` after changing `table-mobile` markup.
- Run `python scripts/check_js_table_labels.py` after changing JavaScript that generates table rows or cells.
- Use `includes/table_empty.html` for table empty rows when possible.
- Product list tables are specialized with `.products-grid` and `.products-mobile`; extend those only in `prices/list.html`/`products.css`.
- Our Products catalogue rows in `prices/our_products_catalog.html` are specialized as parsed read-only rows with a pencil-triggered inline editor; do not render every row as visible inputs by default.
- Our Products collections tab must display collection rows with their brand and submit brand-scoped actions; collection names are not global buckets.
- Fragrantica staged source rows in `prices/fragrantica_products.html` should mirror the Our Products parsed-row structure and keep audience/gender, year, and source link visible.
- Fragrantica staged source rows should show ranked Our Products candidates inline with same-page link actions; avoid forcing operators to navigate to another catalogue page for normal linking.
- Catalogue comparison rows should keep collection names as muted secondary subnames under the primary identity, not inline between brand and scent, so Our Products and Fragrantica rows are easier to compare.
- Linked Our Products/Fragrantica rows are a final linked state: show a distinct linked background and the linked counterpart only, not additional alternative suggestions.
- Our Products linked Fragrantica rows should stay compact: show catalogue collection/year/audience as one metadata row under the product name, render the linked Fragrantica counterpart as inline green text, keep row height tight with the checkbox aligned to the text block, and avoid linked-row background fills, nested linked-match cards, a separate flags column, or repeated informational chips.
- Catalogue row action columns must reserve enough width for text buttons; do not size text-action columns like icon-only columns. On narrow screens, row actions should wrap to the full row width instead of clipping off canvas.
- Catalogue review lists that support row selection should use `prices/js/catalogue-selection.js` with `data-catalogue-selection-root`, row checkboxes, Ctrl/Shift range selection, Escape clear, and page-specific bulk actions instead of one-off selection scripts.
- Catalogue linking workbench selection must allow rows without ready suggestions to be selected and deleted; bulk-link enablement should depend only on selected rows that carry link-pair data.
- Catalogue linking workbench suggestion/confidence filters must fill the current page from matching rows, not only filter the already-paginated 40 rows; after bulk linking, page 1 should continue showing later eligible matches instead of an empty panel while matches remain on later pages.
- Catalogue linking single-row Fragrantica link actions should update the selected row in place with the AJAX response; do not redirect back into a full candidate rebuild unless JavaScript is unavailable.
- Assistant unparsed normalization rows should mirror parsed queue columns and may show bounded, non-persistent parser previews for the visible page; use explicit parse actions to create saved `ParsedSupplierProduct` rows.
- Supplier import spreadsheet previews are specialized scrollable tables. Keep `.import-preview-table` headers visible because they are mapping controls; do not add `.table-mobile` there. Generated preview cells should still use scoped headers and `data-label` metadata.
- Keep actions in `td.actions` or `td[data-label="Actions"]`.

## Filters and search

Preferred dense filter layout:

```html
<form method="get" class="layout-grid-12 gap-md items-end">
  <div class="span-12 md-span-4">
    <label class="form-label" for="field-id">Label</label>
    <input id="field-id" name="q" value="{{ query }}">
  </div>
  <button class="button secondary" type="submit">Apply filters</button>
</form>
```

Simple search pattern:

```html
<form method="get" class="search-wrap">
  <label class="visually-hidden" for="simple-search">Search</label>
  <input id="simple-search" type="text" name="q" value="{{ request.GET.q }}" placeholder="Search...">
  <button class="button ghost" type="submit">Search</button>
</form>
```

Product list search/filter pattern:
- `.search-wrap.filters-products`
- `.search-strip`
- `.search-inline-row`
- `.search-input-wrap`
- `.product-filters-drawer`
- `data-drawer-toggle="product-filters"`
- `prices/_product_list_search_filters.html` renders the repeated product/generic list search strip and drawer. Use it only for `prices/list.html` style product browsers; use grid filters for normal admin/report screens.

- Use drawer filters for the product list pattern; use grid filters for admin/report screens.
- Preserve filter query parameters through pagination.
- Always provide reset/clear behavior when filters can hide data.

## Buttons

Visual hierarchy:
- Primary/default action: `.button` or `.button primary`.
- Secondary action: `.button secondary`.
- Low-emphasis navigation/action: `.button ghost`.
- Destructive action: `.button danger` with `data-confirm`.
- Icon-only action: `.button icon` with `title` and `aria-label`.

Rules:
- Do not add new button colors or shapes.
- Use existing `.button`, `.btn`, `.action-btn` styling; prefer `.button` in templates.
- Every `<button>` must declare an explicit `type`: use `type="submit"` for form submission, `type="button"` for JS/UI controls, and `type="reset"` only for real reset controls.
- JS automatically adds `.btn-icon` to `.button.icon`; keep icon buttons accessible.
- Icon-only `.button.icon` links/buttons must include both `title` and `aria-label`.
- Forms are auto-disabled on submit by `prices/js/app.js`; use `data-no-submit-disable="1"` only when a form intentionally needs repeated submits.
- POST controls with destructive text such as delete, clear, remove, cancel, or exclude must use `data-confirm` on the submit control or the form.
- Delete confirmation links that navigate to a delete-confirmation page must still use `.button danger`; do not style delete links as `.button ghost`.
- Run `python scripts/check_destructive_actions.py` after changing destructive POST actions.
- Run `python scripts/check_template_buttons.py` after changing template buttons.

History-back/cancel controls should be buttons with `data-history-back` and an optional `data-history-fallback`, not `href="javascript:..."` links.

## Forms

- Generic CRUD screens use `prices/form.html` or `.section-card.form-shell`.
- Assistant forms use `.workspace-block.form-shell` when following nearby assistant templates.
- Labels use `.form-label`; helper/error text uses `.form-help`, `.field-errors`, or `.form-errors`.
- Literal `<label for="...">` targets must point to a matching literal `id` in the template or to a Django-rendered form field such as `{{ form.field }}` that renders `id_field`.
- Literal text/search inputs need a programmatic label: visible `<label>`, `aria-label`, `aria-labelledby`, or `title`. Placeholder text alone is not enough.
- Literal `id` values must be unique within a template. When similar branches share behavior, use unique ids plus shared `data-*` hooks for JavaScript.
- `prices/js/app.js` decorates normal inputs with `.form-field`, selects with `.select-field`, and checkboxes with `.check-input`; do not fight those classes.
- Keep form state and validation errors visible after errors.
- Every template `method="post"` form must include `{% csrf_token %}`; run `python scripts/check_template_csrf.py` after changing POST forms.
- Do not rely on browser default submit behavior for buttons inside forms; submit buttons must say `type="submit"`.
- Run `python scripts/check_template_labels.py` after changing labels or form control ids.
- Run `python scripts/check_template_ids.py` after changing literal ids or JavaScript hooks that reference controls.

## Cards, panels, and summaries

- Use `.section-card` for primary framed sections.
- Use `.section-panel` for secondary work panels.
- Use `.workspace-block` in assistant/workbench contexts.
- Use `.metric-card`/`.kpi-link-card` for dashboard count/action cards.
- Use `.card-label`, `.card-title`, and `.card-desc` inside cards/panels.
- Import dashboard summaries use `.import-summary-card` variants in `imports.css`; product/catalog cards use existing `our-products-*` classes.
- For bidirectional catalogue matching, extend the existing `.catalogue-linking-*` two-column workbench pattern instead of adding another one-off link list.
- Catalogue linking workbench height changes must update the desktop `.catalogue-linking-layout` and its internal scroll panels (`.catalogue-linking-list`, `.catalogue-linking-candidates`), then verify against a real desktop viewport. Do not assume changing the outer card alone improves visible working height.
- Do not nest decorative cards inside cards.

## Alerts, empty states, and status

- Django messages render as `.flash` in `prices/base.html`.
- Shared message markup lives in `includes/messages.html`; do not duplicate flash markup in individual pages.
- Inline warnings use `.flash flash--warning`; success/error use `.flash--success`, `.flash--error`, or `.flash--danger`.
- Status badges/chips use existing classes: `.badge`, `.score-badge`, `.import-status-chip`, `.import-health-chip`, `.run-log-status`, `.price-delta-badge`.
- Empty states use `.empty-state`, `.products-empty-state`, or `includes/table_empty.html`.
- Empty states should say what is missing and include a next action only when useful.

## Drawers, dialogs, and UI JavaScript

- Use the existing drawer pattern:
  - trigger: `[data-drawer-toggle="name"]`
  - trigger attributes: `aria-controls="[drawer id]"` and initial `aria-expanded="false"`
  - panel: `.app-drawer` with matching `data-drawer="name"`, `id`, initial `aria-hidden="true"`, and `aria-labelledby` or `aria-label`
  - close button: `[data-drawer-close]` with visible text, `aria-label`, or `title`
  - shared backdrop: `[data-drawer-backdrop]`
- Native `<dialog>` shortcut/help panels must have `aria-labelledby` or `aria-label`.
- `prices/js/app.js` manages drawer open/close, focus return, Escape, Tab trapping, flash dismissal, submit confirms, submit busy states, and table/input class decoration.
- `prices/js/app.js` manages `data-history-back` cancel buttons; do not use `javascript:` href values.
- Queue keyboard shortcuts use `assistant_linking/js/queue-keys.js` with `[data-queue-row]`, `[data-shortcut-dialog]`, and `[data-shortcut-help]`.
- Product search/filter behavior lives in `prices/js/list-search.js`; product-list bulk selection/delete behavior lives in `prices/js/list-bulk.js`. Do not fork either for another product-list screen unless a new reusable hook is needed.
- When JavaScript renders supplier, catalogue, or assistant-provided text, use `textContent`/`createTextNode` instead of concatenating `innerHTML`.
- Run `python scripts/check_template_drawers.py` after changing drawers or native dialogs.
- Run `python scripts/check_js_dom_safety.py` after changing static JavaScript or inline template scripts that render backend data.

## Responsive rules

- Check mobile whenever templates change.
- Existing breakpoints are mainly `max-width: 991.98px` and `max-width: 767.98px`.
- `.desktop-only-flex`, `.mobile-only`, `.mobile-topbar`, and `.app-drawer` control shell responsiveness.
- `.table-mobile` turns rows into stacked cards on mobile using `td::before { content: attr(data-label); }`.
- Product list mobile behavior is special (`.products-mobile`); test row tap, copy/open controls, filters drawer, and sticky search if touched.
- Buttons/actions must wrap rather than overflow.
- Tabs must remain horizontally scrollable on small screens.

## CSS placement

- Shared system styles: `prices/static/prices/css/app.css`.
- General page/workspace styles: `prices/static/prices/css/pages.css`.
- Product list/search styles: `prices/static/prices/css/products.css`.
- Import board/settings/log styles: `prices/static/prices/css/imports.css`.
- Product detail/chart styles: `prices/static/prices/css/detail.css`.

Add CSS to the narrowest appropriate file. If a pattern is reusable across apps, prefer shared CSS and document it here.

Run `python scripts/check_css_static.py` after changing static CSS. The checker enforces balanced braces, blocks merge markers, disallows negative `letter-spacing`, and disallows viewport-scaled font sizes.
Run `python scripts/check_template_inline_styles.py` after changing template visual styling. Templates should not use `style` attributes or `<style>` blocks; put reusable styling in the appropriate static CSS file.
Run `python scripts/check_template_accessibility.py` after changing icon-only actions, image tags, checkbox/radio controls, or text/search inputs.

## Hard rules

- Do not create a new design style when adding a feature.
- First search for an existing similar component.
- Reuse existing classes, spacing, layout wrappers, table structure, button style, pagination style, tabs style, and responsive behavior.
- Tabs must follow the standard `.tabs`/`.tab` structure.
- Pagination must follow `.pagination-shell` structure.
- Tables must follow `.table-wrap` + `.data-table.table-mobile` + `data-label` structure.
- Filters/search forms must use either the grid filter pattern or the product search/drawer pattern.
- Buttons must use the existing visual hierarchy.
- Page titles/actions belong in `.page-header`/`.page-head` with `.page-actions`.
- Do not add template `style` attributes or `<style>` blocks.
- Do not add `javascript:` href values. Use buttons and shared JS hooks for actions.
- Links with `target="_blank"` must include `rel="noopener"`.
- Icon-only action buttons/links must include `title` and `aria-label`.
- Text/search inputs must have an accessible label; placeholder text alone is not enough.
- Buttons must include an explicit valid `type`.
- Label `for` attributes must target existing controls or Django-rendered form fields.
- Literal template ids must be unique; prefer `data-*` hooks for shared JavaScript behavior.
- Image tags must include `alt`, using `alt=""` only for decorative images.
- Letter spacing must be `0` or positive; do not use negative tracking.
- Do not scale font sizes with viewport units.
- Mobile/responsive behavior must be checked whenever templates change.
- If no pattern exists, create the smallest reusable pattern and document it.
