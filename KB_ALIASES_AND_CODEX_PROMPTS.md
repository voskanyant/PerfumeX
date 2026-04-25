# Knowledge-base aliases mined from supplier-link xlsx

Source file: `Номенклатура_поставщиков_для_загрузки_в_новую_базу_1.xlsx`
(91,691 supplier-product → our-product pairs, generated 2026-04-25).

Three structured data files have been written for Codex to consume:

- `tmp/knowledge-base/concentration_aliases.json` — additions to `ConcentrationAlias`
- `tmp/knowledge-base/brand_aliases.json` — additions to `BrandAlias`
- `tmp/knowledge-base/parser_term_additions.json` — extensions to the deterministic parser's term lists (`TESTER_TERMS`, `SAMPLE_TERMS`, etc.) plus regex preprocessing rules

---

## What I learned from the file

**Coverage:** the canonical concentration on every row resolves to one of five classes — Eau de Parfum (60k), Eau de Toilette (19k), Extrait de Parfum (11k), Eau de Cologne (1.4k), Perfume Oil (175). The string `Parfum` is *never* used by the human-curated side, even though `assistant_linking/migrations/0004_concentrationalias.py` seeds `("parfum", "Parfum")`. Every row tagged "Parfum" by suppliers maps to `Extrait de Parfum` — that seed is a bug, fixed in Prompt 1.

**The biggest signal gaps in the current normalizer:**

1. **Truncated and Russian concentration phrases** — `eau de parf` (5,506 rows), `туалетная вода` (1,534), `одеколон` (134), `парфюмированая` (typo), `парф вода` — none currently mapped.
2. **`духи` alone** — 1,304 rows; 98% map to `Extrait de Parfum`. Currently invisible to the parser.
3. **`parfume` / `perfume`** — 360+528 rows; both 90%+ map to `Extrait de Parfum`. Not mapped.
4. **Concentration-as-suffix tokens with no space** — `edp100ml` / `edt100ml` / `100мл` / `100.0ml`. The existing regex handles `edp100ml` but not the others.
5. **Cyrillic-via-Latin-keyboard homoglyphs** — `tectep` (т-е-с-т-е-р typed in a Latin layout, 186 rows) currently parses as garbage; bypassing the existing `тестер` rule.
6. **Damage / condition flags** — `подмят` (382), `декод` (153), `fake` (34), `поврежд` (20), `без коробки` (56) — currently ignored entirely.
7. **Refill markers** — `refill` / `refil` (1,629 combined) — currently ignored.
8. **Brand abbreviations** — `ysl` → Yves Saint Laurent (577), `c.dior` → Dior (365), `d&g` → Dolce & Gabbana (442), plus a tail of `&` vs `and` vs whitespace variants (Viktor & Rolf, Zadig & Voltaire, Roos & Roos, Astrophil & Stella, Goldfield & Banks, Philly & Phill, M. Micallef, S.T. Dupont, etc.).
9. **Cyrillic brand renderings** — only a handful are frequent enough to alias: `норана` / `noran` → Norana Perfumes (242 combined), `экс нихило` → Ex Nihilo (164), `марли` → Parfums de Marly (43).
10. **Skip-listed deliberately** — `mont` (collides Montale / Montblanc / Ormonde Jayne), `lm` (Vilhelm / Laurent Mazzone / Balmain), `c.l` (10:1 Acqua di Parma vs Christian Lacroix), `крид` (only 7 hits). Don't alias these — they'd cause more false positives than they'd fix.

Total alias additions: **25 concentration aliases**, **25 brand aliases**, **~70 parser term additions** across tester / sample / travel / set / no-box / damage / refill / region-stopword lists, plus **3 regex preprocessing rules**.

---

# Codex Prompts

> **Standing instructions for Codex (paste once at the start of the session):**
>
> ```
> You are working on the PerfumeX Django 5 / PostgreSQL repo. The deterministic
> parser lives in assistant_linking/services/normalizer.py and the alias models
> are in assistant_linking/models.py + assistant_core/models.py (for GlobalRule).
> Read README.md and assistant_linking/migrations/0004_concentrationalias.py
> first. After every prompt, run:
>     python manage.py makemigrations --check --dry-run
>     python manage.py test assistant_linking assistant_core
> and report the diff. Don't commit.
> ```

---

## PROMPT 1 — Add new ConcentrationAliases (data migration)

**What this fixes:** `eau de parf`, `туалетная вода`, `одеколон`, `духи`, `parfume`, `attar`, `roll on`, etc. Plus rewrites the wrong existing seed `parfum → Parfum` to `parfum → Extrait de Parfum`.

```
Read these files first:
- tmp/knowledge-base/concentration_aliases.json   (data to load)
- assistant_linking/migrations/0004_concentrationalias.py  (existing seed pattern)
- assistant_linking/models.py                     (ConcentrationAlias shape)

Generate a new migration `assistant_linking/migrations/0005_seed_concentration_aliases_from_corpus.py`
modeled on 0004. It must:

1. Depend on the latest assistant_linking migration (verify with `ls
   assistant_linking/migrations/`).

2. Have a forward function that:
   - Reads `tmp/knowledge-base/concentration_aliases.json` from the repo
     root using a relative path resolved at migration time.
   - For each entry in `additions`, calls `ConcentrationAlias.objects
     .get_or_create(alias_text=..., supplier=None, concentration=...,
     defaults={normalized_alias=alias_text, active=True, priority=...,
     is_regex=False})`. Skip if already present.
   - For each entry in `rewrite_existing`, finds the matching row and
     updates `concentration` to the new value. Use a single
     `.update()` query, not get-then-save. Log via
     `print(f"rewrote N rows ...")` so the migration output is auditable.

3. Have a backward function that:
   - Deletes only the rows added by `additions` (match on alias_text +
     supplier_id IS NULL + concentration).
   - Reverts the `rewrite_existing` rewrites by setting concentration
     back to the original value.

4. NOT inline the alias list in the migration body — keep it loaded from
   the JSON so future updates can edit JSON without writing new
   migrations. Add a docstring to the migration explaining this.

5. Include a `migrations.RunPython.noop`-fallback if the JSON file is
   missing, with a `print` warning, so a CI run on a checkout without the
   tmp/ directory doesn't crash.

Then add a test in
`assistant_linking/tests/test_concentration_aliases_corpus.py`:
- Run the migration forward and assert a sample of 5 aliases (one per
  canonical class) round-trip correctly through `parse_supplier_product`
  on a synthetic SupplierProduct: e.g. supplier_name "Foo Brand туалетная
  вода 50ml" → result.concentration == "Eau de Toilette".

After: run `python manage.py migrate assistant_linking` against a fresh
local DB and paste the row count diff for ConcentrationAlias.
```

---

## PROMPT 2 — Add new BrandAliases (data migration)

**What this fixes:** `ysl`, `c.dior`, `d&g`, `viktor&rolf`, `m.micallef`, plus Russian renderings like `норана`, `экс нихило`, `марли`.

```
Read these files first:
- tmp/knowledge-base/brand_aliases.json
- assistant_linking/models.py (BrandAlias)
- assistant_linking/migrations/0001_initial.py for any existing brand
  alias seeds (grep for "BrandAlias")

Generate `assistant_linking/migrations/0006_seed_brand_aliases_from_corpus.py`.

It must:

1. Depend on 0005 (the concentration migration from Prompt 1) and on the
   latest catalog migration (since BrandAlias.brand FK is to catalog.Brand).

2. Forward function:
   - Reads tmp/knowledge-base/brand_aliases.json.
   - For each [alias_text, brand_name, priority] entry, looks up the
     Brand by `Brand.objects.filter(name__iexact=brand_name).first()`.
   - If the brand is missing, prints a warning and skips that entry —
     do NOT raise — so the migration succeeds on minimal/test fixtures.
   - Creates the BrandAlias via get_or_create with:
        alias_text=alias_text,
        supplier=None,
        brand=brand,
        defaults={
            normalized_alias=normalize_alias_value(alias_text),
            active=True,
            priority=priority,
            is_regex=False,
        }
     Use the historical model's normalize helper if importable; else
     inline a copy of `normalize_alias_value` from
     assistant_linking/models.py.

3. Backward function: delete each (alias_text, supplier=None, brand=brand).

4. Print a one-line summary at the end:
   `created N brand aliases, skipped M (brand missing)`

Add a test in
`assistant_linking/tests/test_brand_aliases_corpus.py`:
- Create a Brand "Yves Saint Laurent". Run the migration. Build a
  SupplierProduct with name "YSL Black Opium edp 50ml" and assert
  `parse_supplier_product(...)` returns
  result.normalized_brand.name == "Yves Saint Laurent".
- Repeat the round-trip for "d&g" → Dolce & Gabbana and "норана" →
  Norana Perfumes.

After: confirm `python manage.py test assistant_linking` passes.
```

---

## PROMPT 3 — Expand parser term lists + add regex preprocessing

**What this fixes:** Tester homoglyphs (`tectep`), refill markers, damage flags, no-box variants, decant/sample variants, Russian travel/miniature words, compact size formats (`100.0ml`, `100мл`), truncated `eau de parf`.

```
Read these files first:
- tmp/knowledge-base/parser_term_additions.json
- assistant_linking/services/normalizer.py
- assistant_linking/services/garbage.py

Modify `assistant_linking/services/normalizer.py` as follows:

1. Extend the existing tuples at the top of the file using the values from
   parser_term_additions.json:
       TESTER_TERMS  +=  TESTER_TERMS_additions   (strip out comment text)
       SAMPLE_TERMS  +=  SAMPLE_TERMS_additions
       TRAVEL_TERMS  +=  TRAVEL_TERMS_additions
       SET_TERMS     +=  SET_TERMS_additions
       NO_BOX_TERMS  +=  NO_BOX_TERMS_additions
   Keep tuples sorted by length DESC so longer phrases match before their
   substrings (e.g., "no spray" before "no").

2. Add two new module-level tuples:
       DAMAGE_TERMS = (...)   # from DAMAGE_TERMS_new_list
       REFILL_TERMS = (...)   # from REFILL_TERMS_new_list

3. In `parse_supplier_product`, after the existing block that sets
   `result.is_tester / is_sample / is_travel / is_set / packaging`, add:

       result.is_refill = _contains_any_phrase(text, REFILL_TERMS)
       result.is_damaged = _contains_any_phrase(text, DAMAGE_TERMS)
       if result.is_refill:
           result.modifiers.append("refill")
       if result.is_damaged:
           result.modifiers.append("damaged")
           result.warnings.append("damaged-condition flag detected")

   Note: the ParsedSupplierProduct model does NOT need new boolean
   columns for these — store them via `modifiers` (JSONField). If you'd
   rather add columns, do it in a separate migration; for now, modifier
   strings are enough.

4. In `_strip_known_terms` call near the bottom of `parse_supplier_product`,
   include the new term lists so they're stripped from the residual
   product-name text:
        *DAMAGE_TERMS, *REFILL_TERMS,

5. Update `normalize_text(value: str)` to apply the
   REGEX_PREPROCESS_additions from the JSON. Order matters — apply
   "truncated-eau-de-parf" BEFORE the existing edp/edt/edc-attached-digit
   normalization. Apply "compact-rus-no-space" BEFORE
   "compact-mil-with-decimal-zero" (so "100.0мл" -> "100 ml" via two
   passes). After your changes, the function should still:
   - lowercase
   - NFKC-normalize
   - collapse whitespace
   - leave alphanumerics intact

6. Add unit tests in `assistant_linking/tests/test_normalizer.py`:
   - `test_normalize_text_eau_de_parf_expands` — input "Foo eau de parf
     50ml" → output contains "eau de parfum".
   - `test_normalize_text_compact_rus_size` — input "Foo 100мл" → output
     "foo 100 ml".
   - `test_normalize_text_decimal_zero_size` — input "Foo 100.0ml" →
     output "foo 100 ml".
   - `test_parse_handles_tectep` — SupplierProduct with name "Brand X
     50ml tectep edp" → result.is_tester is True.
   - `test_parse_marks_refill` — name "Brand Y edp refil 100ml" →
     "refill" in result.modifiers.
   - `test_parse_marks_damaged` — name "Brand Z edp 100ml подмят" →
     "damaged" in result.modifiers and a damaged-condition warning.
   - `test_parse_handles_dukhi_alone` — name "Brand Q духи 50ml" →
     result.concentration == "Extrait de Parfum" (relies on Prompt 1).

After: run the full normalizer test suite. Report any tests that broke
because of the broader stripping — likely none, but stripped-text
side-effects can surface.
```

---

## PROMPT 4 — Add region/origin stopwords as garbage rules (or as parser stopwords)

**What this fixes:** Origin / certification noise (`франция`, `маркированный`, `оригинал`, `шт`) that's not a real attribute of the perfume but adds tokens that confuse fuzzy matching.

```
Read parser_term_additions.json — REGION_STOPWORDS_new_list — and decide
how to apply each one. There are two viable homes:

Option A (preferred for region words):
   Add a new module-level tuple in
   assistant_linking/services/normalizer.py:

       REGION_STOPWORDS = (
           "франция", "оаэ", "эмираты", "kuwait", "saudi",
           "made in italy", "made in france",
           "марк", "маркированный", "маркированая", "марка",
           "оригинал", "оригинальный", "шт",
       )

   Strip these in normalize_text() at the very END of the function,
   before the final whitespace collapse:

       for stop in REGION_STOPWORDS:
           text = re.sub(rf"(^|\s){re.escape(stop)}($|\s)", " ", text)

   This removes the noise from the normalized text used for matching,
   without affecting how it's stored in raw_name.

   IMPORTANT: be careful with "марка" — it also legitimately means
   "brand" in non-product contexts. Add it to REGION_STOPWORDS only if
   no test fails. If any test breaks, drop "марка" but keep "марк" and
   "маркированный".

Option B (for damage/fake words):
   Re-use the existing `match_garbage_keyword` / GlobalRule system in
   assistant_core. Add a data migration in assistant_core that creates
   GlobalRule rows with rule_kind="garbage_keyword" for fake/counterfeit
   indicators that should EXCLUDE a product entirely (not just flag it):

       fake, counterfeit, реплика, копия, китай, китайский,
       подделка, подделки

   These are NOT mined from the corpus (the corpus is curated, so it
   doesn't contain known-bad rows) but they're worth seeding now since
   suppliers will eventually try to slip them through.

   Generate `assistant_core/migrations/000X_seed_fake_garbage_rules.py`
   modelled on existing data migrations. Use rule_kind="garbage_keyword",
   approved=True, active=True, priority=100.

After both changes:
- Re-run `python manage.py test`.
- Run a manual smoke test: create a SupplierProduct with name
  "Bvlgari Aqva Pour Homme edp 100ml оригинал маркированный франция" and
  call `parse_supplier_product`. Assert that
  `result.normalized_text` does NOT contain those three stopwords.
```

---

## PROMPT 5 — Audit screen: surface what the parser is doing

**What this fixes:** "I don't know if my new aliases are working." Adds a small admin diagnostic page so you can paste a raw supplier name and see exactly what the parser extracts.

```
Read assistant_linking/views.py and assistant_linking/urls.py. There's
an existing review-queue UI; add ONE small staff-only diagnostic page
adjacent to it.

1. Add a view `ParseDryRunView` (FormView) at
   `/admin/assistant/linking/parse-dry-run/`:
   - GET: shows a textarea + supplier-select dropdown + submit button.
   - POST: accepts {raw_name, supplier_id (optional)} and renders the
     same page with a structured display of what the parser produced:
         normalized_text
         detected_brand_text → resolved Brand
         concentration  (and which alias matched)
         size_ml + raw_size_text
         supplier_gender_hint
         packaging / variant_type
         is_tester / is_sample / is_travel / is_set / is_refill / is_damaged
         modifiers
         warnings
         confidence

   - Build a temporary in-memory `SupplierProduct(name=raw_name,
     supplier_id=supplier_id)` and call `parse_supplier_product(...)`.
     Do NOT persist anything.

2. To make the "which alias matched" trace visible, modify
   `parse_supplier_product` to optionally return a list of debug events
   alongside the result when `debug=True`:
       result.debug_trace = [
           {"step": "concentration", "matched_alias": "духи",
            "value": "Extrait de Parfum"},
           {"step": "brand", "matched_alias": "ysl",
            "brand": "Yves Saint Laurent"},
           {"step": "size", "raw": "100ml", "value": Decimal("100")},
           ...
       ]
   Have ParseDryRunView pass debug=True; default everywhere else stays
   off.

3. Template: `assistant_linking/templates/assistant_linking/parse_dry_run.html`
   - Use the existing custom-admin layout (extend whatever the workbench
     templates extend).
   - Show input on top, results below in a 2-column grid:
     left = parsed fields, right = debug_trace.
   - Add a tiny "examples" link that pre-fills the textarea with one of
     these tricky cases: "Bvlgari Aqva PH edp 100ml tectep маркированный",
     "YSL Black Opium духи 50ml уни", "норана Bushido edp 100ml refil".

4. Wire URL in assistant_linking/urls.py and add a link from the
   linking workbench navigation.

5. Test in assistant_linking/tests/test_parse_dry_run.py:
   - Non-staff user → 403.
   - Staff user GET → 200 with form.
   - Staff user POST with the YSL example → response body contains
     "Yves Saint Laurent" and "Eau de Parfum".

After: explain in the PR description how to use the page, and add one
sentence to PROJECT_HANDOFF.md telling future staff "if a supplier name
isn't being parsed correctly, paste it into /admin/assistant/linking/
parse-dry-run/ before opening a bug."
```

---

## PROMPT 6 — Reparse all supplier products after migrations

**What this fixes:** New aliases only affect products that get re-parsed. Run the bulk reparse.

```
After Prompts 1-4 are merged and migrated, run a one-shot bulk reparse
so existing SupplierProduct rows pick up the new alias coverage.

Use the existing management command:
    python manage.py reparse_supplier_products

If that command does not have a --batch-size or --since flag, edit
assistant_linking/management/commands/reparse_supplier_products.py to
add:
    --batch-size N    (default 1000)
    --skip-locked     (default True — never overwrite locked_by_human)
    --confidence-lt N (only reparse rows with confidence < N)
    --dry-run

Then run two passes:
    python manage.py reparse_supplier_products --confidence-lt 80 \
        --skip-locked --batch-size 1000
    python manage.py reparse_supplier_products --confidence-lt 100 \
        --skip-locked --batch-size 1000

Capture before/after counts:
    SELECT
      COUNT(*) FILTER (WHERE concentration = '') AS missing_concentration,
      COUNT(*) FILTER (WHERE size_ml IS NULL) AS missing_size,
      COUNT(*) FILTER (WHERE normalized_brand_id IS NULL) AS missing_brand,
      COUNT(*) FILTER (WHERE is_tester) AS testers,
      AVG(confidence)::int AS avg_conf
    FROM assistant_linking_parsedsupplierproduct;

Run that query before and after, and put both result rows in the PR
description so the impact of the new aliases is documented.
```

---

## How to use this file

Run the prompts **in order, one at a time**. Each one assumes the previous one is merged.

The data files in `tmp/knowledge-base/` are intentionally kept *outside* the migrations themselves — they're the source-of-truth that the migrations read at run time. If you mine more aliases from another spreadsheet later, edit the JSON and add a follow-up data migration that re-applies it via `get_or_create` (idempotent). No need to write new alias literals into Python.

The skip-list of intentionally-not-aliased tokens (`mont`, `lm`, `c.l`, `крид`) is documented inside `brand_aliases.json` — if a future analysis wants to revisit them, it's recorded why each was rejected.
