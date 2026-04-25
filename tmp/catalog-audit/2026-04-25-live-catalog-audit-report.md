# PerfumeX Live Catalogue Audit

Date: 2026-04-25

Scope:
- Live server audit over SSH, read-only only
- Catalogue reviewed from the production database at `77.233.213.208`
- No edits made to database or application code
- Product identity treated as `brand + scent name + concentration`

Method:
- Exported the full live perfume catalogue from Django/PostgreSQL
- Ran full-catalog heuristic checks across all rows
- Manually reviewed the strongest candidates
- Verified high-confidence findings against official brand sites first, with retailer/community sources only as support when needed

Live catalogue snapshot:
- Brands: `1009`
- Perfumes: `16238`
- Variants: `38501`

Heuristic sweep summary over the full live perfume export:
- Exact duplicate `brand + name + concentration`: `0`
- Near-duplicate name pairs flagged: `1176`
- Comma/bundle-style names flagged: `61`
- Containment/name-split cases flagged: `302`
- Same normalized scent across multiple concentrations: `1000`
- Plain-name vs gendered-name cases flagged: `118`

## High-confidence findings

### 1. Initio `Musk Therapy` likely miscatalogued as two concentrations
Problem:
- Live catalogue currently contains both:
  - `8076 | Initio | Musk Therapy | Eau de Parfum`
  - `5636 | Initio | Musk Therapy | Extrait de Parfum`
- Initio official pages currently present `Musk Therapy` as `Extrait de Parfum`, not as a separate `Eau de Parfum` product.

Evidence:
- Official Initio product page: [Musk Therapy](https://us.initioparfums.com/products/musk-therapy)
  - page states `EXTRAIT DE PARFUM`

Assessment:
- High-confidence mistake
- Most likely action later: remove or rename the local `Musk Therapy | Eau de Parfum` row after checking whether any variants or content were attached to the wrong concentration

### 2. Clive Christian `Blonde Amber` and `Noble XXI: Art Deco - Blonde Amber` look like the same perfume split into two catalogue rows
Problem:
- Live catalogue currently contains:
  - `6598 | Clive Christian | Blonde Amber | Extrait de Parfum`
  - `10779 | Clive Christian | Noble XXI: Art Deco - Blonde Amber | Extrait de Parfum`

Evidence:
- Official product page: [Blonde Amber](https://us.clivechristian.com/products/art-deco-blonde-amber-1)
  - page title and breadcrumb resolve to `Blonde Amber`
  - product belongs to `NOBLE COLLECTION`
- Official Clive Christian article: [A Definitive Guide to the Noble Collection](https://us.clivechristian.com/blogs/new-collection-launch/a-definitive-guide-to-the-noble-collection)
  - describes `NOBLE XXI ART DECO` as the era/story context
  - then lists the perfume itself as `BLONDE AMBER`

Assessment:
- High-confidence duplicate naming split
- `Noble XXI / Art Deco` appears to be collection/story framing, not a distinct scent name that should create a second perfume record

### 3. Dolce & Gabbana `Q Intense Pour Homme` appears wrong
Problem:
- Live catalogue currently contains:
  - `8059 | Dolce & Gabbana | Q Intense | Eau de Parfum`
  - `5045 | Dolce & Gabbana | Q Intense by Pour Femme | Eau de Parfum`
  - `8058 | Dolce & Gabbana | Q Intense Pour Homme | Eau de Parfum`
- The `Q` line is the women's line; `K` is the men's line.

Evidence:
- Official D&G page: [Q by Dolce&Gabbana Eau de Parfum Intense](https://www.dolcegabbana.com/en-us/beauty/perfumes-for-her/q-by-dolceandgabbana/q-by-dolceandgabbana-eau-de-parfum-intense---158057971187843.html)
  - page is under `perfumes-for-her`
  - explicitly presents `Q by Dolce&Gabbana Eau de Parfum Intense` as feminine
- Fragrantica support page: [Q by Dolce & Gabbana Eau de Parfum Intense](https://www.fragrantica.com/perfume/Dolce-Gabbana/Q-by-Dolce-Gabbana-Eau-de-Parfum-Intense-89376.html)
  - describes it as a fragrance for women

Assessment:
- High-confidence mistake
- `Q Intense Pour Homme` is very likely a wrong row
- The men's counterpart should belong to the `K` line, not the `Q` line

### 4. Dolce & Gabbana women's `Light Blue` naming is inconsistent
Problem:
- Live catalogue currently contains:
  - `5102 | Dolce & Gabbana | Light Blue Eau Intense | Eau de Parfum`
  - `18662 | Dolce & Gabbana | Light Blue Eau Intense Pour Femme | Eau de Parfum`
  - `6096 | Dolce & Gabbana | Light Blue Eau Intense Pour Homme | Eau de Parfum`
- The women's Light Blue collection is presented on the official site without `Pour Femme`, while the men's line uses `Pour Homme`.

Evidence:
- Official women's collection page: [Light Blue Women's Perfumes](https://www.dolcegabbana.com/en-us/beauty/perfumes-for-her/light-blue/)
  - shows `Light Blue Eau De Parfum` and other women's Light Blue products
  - does not present a separate `Pour Femme` naming convention on the women's line
- Official men's collection page: [Light Blue pour Homme](https://www.dolcegabbana.com/en-us/beauty/perfumes-for-him/light-blue-pour-homme/)
  - explicitly uses `Pour Homme`

Assessment:
- High-confidence naming inconsistency
- Most likely the women's row should be one canonical perfume, not both `Light Blue Eau Intense` and `Light Blue Eau Intense Pour Femme`

### 5. Initio bundle row should not exist as a perfume
Problem:
- Live catalogue contains:
  - `5357 | Initio | Paragon, Musk Therapy, Rehab | Extrait de Parfum`
- This reads like a set/bundle/marketing grouping, not one perfume identity.

Evidence:
- Official Initio site lists `Paragon`, `Musk Therapy`, and `Rehab` as separate fragrances/products, each with their own page family
- The comma-separated local name strongly indicates a grouped set or import artifact

Assessment:
- High-confidence non-catalogue row
- Should be reviewed as probable bundle/set contamination in the perfume table

## Strong manual-review candidates

These are not all confirmed mistakes, but they are high-value review buckets produced from the full live sweep.

### A. Brand hotspots by near-duplicate naming
Top brands by flagged near-duplicate pairs:
- Amouage: `44`
- Roja: `37`
- Graff: `36`
- Escentric Molecules: `30`
- Bois 1920: `29`
- Kenzo: `24`
- Guerlain: `23`
- Calvin Klein: `22`
- Armani: `20`
- Dolce & Gabbana: `18`

### B. Brand hotspots by concentration inconsistency
Top brands by same normalized scent appearing in multiple concentrations:
- Armani: `27`
- Atelier Cologne: `14`
- Boadicea the Victorious: `10`
- Azzaro: `7`
- Annick Gooutal: `6`
- Abercrombie & Fitch: `5`
- Balmain: `5`
- 12 Parfumeurs: `4`
- Amouroud: `4`
- Banana Republic: `4`

Important note:
- Some of these will be legitimate because the brand truly sells the scent in multiple concentrations.
- This bucket is for review priority, not automatic correction.

### C. Brand hotspots by plain-name vs gendered-name inconsistency
Top brands flagged where a plain row may actually duplicate a gendered one:
- Dolce & Gabbana: `9`
- Armani: `8`
- Dsquared2: `6`
- Eisenberg: `5`
- Gucci: `5`
- Guerlain: `4`
- Issey Miyake: `4`

Examples worth checking:
- `Armani | Code` alongside `Armani | Code Pour Homme`
- `Dolce & Gabbana | Light Blue Eau Intense` alongside `... Pour Homme`

### D. Bundle/set contamination candidates
Examples that look like sets or grouped products rather than one perfume:
- `5357 | Initio | Paragon, Musk Therapy, Rehab | Extrait de Parfum`
- `5358 | Clive Christian | No. 1 Feminine + Masculine | Extrait de Parfum`
- `5356 | Clive Christian | Queen Anne Rock Rose + Cosmos Flower | Extrait de Parfum`
- `5355 | Clive Christian | Rococo Immortelle + Rococo Magnolia Flower | Extrait de Parfum`

These rows should be reviewed carefully before publication or downstream linking.

## Notable examples from the heuristic sweep

### Clive Christian
- `6598 | Blonde Amber | Extrait de Parfum`
- `10779 | Noble XXI: Art Deco - Blonde Amber | Extrait de Parfum`

### Initio
- `8076 | Musk Therapy | Eau de Parfum`
- `5636 | Musk Therapy | Extrait de Parfum`
- `8479 | Paragon | Eau de Parfum`
- `4557 | Paragon | Extrait de Parfum`
- `8466 | Rehab | Eau de Parfum`
- `5610 | Rehab | Extrait de Parfum`
- `5954 | Rehab Extrait | Eau de Parfum`
- `5357 | Paragon, Musk Therapy, Rehab | Extrait de Parfum`

### Dolce & Gabbana
- `5102 | Light Blue Eau Intense | Eau de Parfum`
- `18662 | Light Blue Eau Intense Pour Femme | Eau de Parfum`
- `6096 | Light Blue Eau Intense Pour Homme | Eau de Parfum`
- `5046 | Q | Eau de Parfum`
- `10727 | Q | Extrait de Parfum`
- `8059 | Q Intense | Eau de Parfum`
- `5045 | Q Intense by Pour Femme | Eau de Parfum`
- `8058 | Q Intense Pour Homme | Eau de Parfum`
- `5114 | K Intense | Eau de Parfum`

## Deliverables created

Source files used for this audit:
- `tmp/catalog-audit/perfumex_perfume_audit.csv`
- `tmp/catalog-audit/perfumex_perfumes.csv`
- `tmp/catalog-audit/suspect_name_pairs.json`
- `tmp/catalog-audit/heuristic_summary.json`

## Recommended next step

Do not apply bulk fixes yet.

Recommended review order:
1. High-confidence findings in this report
2. Bundle/set contamination rows
3. Dolce & Gabbana / Armani / Clive Christian / Initio naming inconsistencies
4. The remaining near-duplicate and multi-concentration buckets brand by brand

If you approve the report quality, the next phase should be:
- convert approved findings into a clean action sheet
- then make controlled catalogue corrections in batches
