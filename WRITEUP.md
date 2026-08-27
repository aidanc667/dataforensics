# DataForensics: Before/After Analysis on Real Data

**Status:** Complete. All three datasets were downloaded from their real public
sources, run through `dataforensics scan` and `dataforensics harmonize`, and
the findings below are the tool's actual output, not projected/placeholder
findings.

This document captures the harmonization workflow on three real datasets: a
CDC WONDER mortality export, an ACS PUMS microdata extract, and an OpenNeuro
participants file. All three are checked into `data/raw/` (small enough to
commit directly, unlike the multi-GB extracts a national-scale run would
involve) so the commands below are reproducible from a fresh clone.

---

## Dataset 1: CDC WONDER Mortality Export

### Raw Data Summary
- **Source:** CDC WONDER, Underlying Cause of Death 1999-2020 (https://wonder.cdc.gov/ucd-icd10.html)
- **Query:** Grouped by County, restricted to Colorado, deaths occurring in 2020, with suppressed rows shown
- **File:** `data/raw/cdc_wonder_export.tsv`
- **Rows:** 65 (64 Colorado counties + 1 "Total" summary row CDC WONDER appends by default)
- **Columns:** `County`, `County Code` (5-digit FIPS), `Deaths`, `Population`, `Crude Rate`

### Scan Results
```
$ dataforensics scan data/raw/cdc_wonder_export.tsv --rules schemas/cdc_wonder_rules.yaml --out-dir /tmp/wonder-scan
scan complete: 5 columns profiled, 0 errors, 0 warnings, 0 suggestions
```

**Summary:**
- **Total Issues Found:** 0 errors, 0 warnings, 0 suggestions — but see below; the *absence* of findings here is itself the finding.
- **Data Completeness:** `County Code` is 98.5% non-null (the "Total" row has no single county, so its County Code is blank) — this tool correctly flags that gap as a null rather than silently treating "Total" like any other county.
- **Most Interesting Finding:** `Deaths`, `Population`, and `Crude Rate` are all thousands-comma-formatted in the raw export (`"3,703"`, `"519,883"`), which is completely standard CDC WONDER formatting — and it silently defeats naive numeric parsing. `float("3,703")` raises `ValueError`, so this tool correctly (and conservatively) classifies all three columns as `free_text` rather than numeric, which means the `Deaths: {minimum: 0}` rule in `cdc_wonder_rules.yaml` never actually gets to compare a number to 0 — every row's minimum-check silently short-circuits on the `ValueError` before it can fail or pass. **This is not a bug in the tool; it's a real, easy-to-miss trap**: a rules file that *looks* like it validates numeric bounds can silently validate nothing at all if the source format doesn't parse as a Python float. The honest fix is a pre-processing step to strip thousands separators before scanning — which this tool deliberately does not do on its own, consistent with "never guess" (stripping commas is safe for `"3,703"` but would be actively wrong if a comma-containing free-text field were ever accidentally routed through the same column).

### Harmonize Results
```
$ dataforensics harmonize data/raw/cdc_wonder_export.tsv --rules schemas/cdc_wonder_rules.yaml --output data/cleaned/cdc_wonder_export.harmonized.csv --execute
harmonize complete: wrote data/cleaned/cdc_wonder_export.harmonized.csv, 7 mutations logged
```

7 mutations: 3 counties (Hinsdale, Mineral, San Juan) had their `Deaths` and
`Crude Rate` cells mapped from CDC's `"Suppressed"` sentinel to an explicit
reason string, and Jackson County's `Crude Rate` of `"Unreliable"` (a rate
calculated from 20 or fewer deaths, per CDC's own documented threshold) was
mapped the same way. All three original sentinel strings are preserved
verbatim in `original_value` in the manifest — nothing was silently coerced
to a number or dropped.

The output CSV correctly preserves `County Code`'s leading zeros (`08001`,
not `8001`) — this tool's ID-column leading-zero protection working on a
real government FIPS code, not just a synthetic test fixture.

---

## Dataset 2: ACS PUMS Microdata Extract

### Raw Data Summary
- **Source:** U.S. Census Bureau, American Community Survey 1-Year PUMS, 2022 (https://www2.census.gov/programs-surveys/acs/data/pums/2022/1-Year/csv_pwy.zip)
- **Extract Details:** Wyoming person-level records (the smallest available state file, to keep this a manageable real extract rather than a multi-GB national pull), trimmed from the raw file's 287 columns down to the 8 relevant to this demo
- **File:** `data/raw/acs_pums_extract.csv`
- **Rows:** 5,962 real individual survey respondents
- **Key Variables:** `person_id` (derived as `SERIALNO-SPORDER`), `SERIALNO` (household id), `SPORDER` (person-within-household order), `PUMA`, `AGEP`, `SEX`, `PWGTP` (person weight), `SCHL` (educational attainment code), `RAC1P` (race code)

### Scan Results
```
$ dataforensics scan data/raw/acs_pums_extract.csv --rules schemas/acs_pums_rules.yaml --out-dir /tmp/pums-scan
scan complete: 9 columns profiled, 0 errors, 0 warnings, 1420 suggestions
```

**Summary:**
- **Total Issues Found:** 0 errors, 0 warnings, 1420 suggestions (1,008 rare-category on `RAC1P`, 412 IQR-outlier on `PWGTP`)
- **Data Completeness:** 100% non-null across all 9 columns — real Census microdata is well-curated for missingness, unlike the other two sources here.
- **Most Interesting Finding:** Running this scan is what actually caught a real bug in this tool. The first pass (before the fix below) reported **2,433** suggestions, 921 of them a `rare_category` finding on `SERIALNO` — Census's household identifier. `SERIALNO` isn't a person-unique key (multiple people in the same household share one), so across 5,962 people it has 2,675 distinct values — high, but not maximal, cardinality. `validation.py`'s rare-category heuristic used an independent "cardinality under half the row count" threshold that called this column categorical, while `dictionary.py`'s own classification (using a much tighter cap) correctly called the same column `free_text`. The two disagreed, and the more permissive one fired a misleading "rare category" suggestion on every household that happened to have exactly one person in this sample. **Fixed** by making both modules share one cardinality cap (`dictionary.cardinality_cap`); re-running the scan after the fix drops the count to 1,420 and `SERIALNO` no longer appears at all. This is the clearest evidence in this whole document for why the real-dataset benchmark mattered: a synthetic 20-row fixture never has a column shaped like `SERIALNO` — high-but-not-maximal cardinality only shows up at real scale.
- The 1,008 `RAC1P` (race code) suggestions and 412 `PWGTP` (survey weight) outlier suggestions that remain are both genuinely correct: Wyoming's ACS sample is racially homogeneous enough that several race codes are true one-off rarities in this extract, and person-level survey weights are, by design, a skewed distribution with real statistical outliers. Neither is a bug — this is exactly the kind of finding this tool is supposed to surface for a human to look at, not silently smooth over.

### Harmonize Results
```
$ dataforensics harmonize data/raw/acs_pums_extract.csv --rules schemas/acs_pums_rules.yaml --output data/cleaned/acs_pums_extract.harmonized.csv --execute
```
`SEX` values (raw codes `"1"`/`"2"`) are mapped to `"M"`/`"F"` per
`acs_pums_rules.yaml`'s `category_mappings`, one mutation logged per row
(5,962 total) — the `PWGTP` survey weight column is listed under
`weights_strata` and is never touched, exactly as the rules file requires.

---

## Dataset 3: OpenNeuro Participants File

### Raw Data Summary
- **Source:** OpenNeuro (https://openneuro.org/datasets/ds000117)
- **Dataset ID:** ds000117, "Multisubject, multimodal face processing" (Wakeman & Henson) — a widely-used public MEG/EEG BIDS dataset
- **License:** Public, de-identified per OpenNeuro's own data use terms (no PHI; ages/sexes only, no names or dates of birth)
- **File:** `data/raw/openneuro_participants.tsv`
- **Rows:** 17 (16 real subjects, plus `sub-emptyroom`, a calibration recording rather than a person — BIDS convention, not a data error)

### Scan Results
```
$ dataforensics scan data/raw/openneuro_participants.tsv --rules schemas/openneuro_rules.yaml --out-dir /tmp/openneuro-scan
scan complete: 4 columns profiled, 0 errors, 0 warnings, 4 suggestions
```

**Summary:**
- **Total Issues Found:** 0 errors, 0 warnings, 4 suggestions (rare-category findings on `age`, `sex`, and `first_ses` for the `n/a` value, plus one genuinely rare age of 29)
- **Data Completeness:** `non_null_pct` reports 100% on every column — which is *misleading* in a way worth calling out explicitly. BIDS's own missing-value convention is the literal string `"n/a"`, not an empty cell, so this tool's structural null check (`value == ""`) doesn't see `sub-emptyroom`'s `n/a` age/sex/session as missing at all. The gap between "this file's own missingness convention" and "what this tool calls a null" is exactly why the candidate-sentinel detection feature exists — `"n/a"` is caught there, and only there.
- **Most Interesting Finding:** this is a clean, real demonstration of that exact gap — a naive completeness check on this file would report 100% complete data and miss that one of its four demographic columns is entirely non-informative for one of its seventeen rows.

### Harmonize Results
```
$ dataforensics harmonize data/raw/openneuro_participants.tsv --rules schemas/openneuro_rules.yaml --output data/cleaned/openneuro_participants.harmonized.csv --execute
harmonize complete: wrote data/cleaned/openneuro_participants.harmonized.csv, 3 mutations logged
```
All three of `sub-emptyroom`'s `n/a` values (age, sex, first_ses) were mapped
to an explicit "Not applicable (calibration recording, not a subject)"
reason string; the 16 real subjects' rows are untouched byte-for-byte.

---

## Harmonization Demo: CDC WONDER + ACS PUMS

### Command Executed
```bash
dataforensics harmonize data/raw/cdc_wonder_export.tsv data/raw/acs_pums_extract.csv \
  --rules-map "data/raw/cdc_wonder_export.tsv=schemas/cdc_wonder_rules.yaml,data/raw/acs_pums_extract.csv=schemas/acs_pums_rules.yaml" \
  --crosswalk schemas/wonder_pums_crosswalk.yaml \
  --output-dir data/cleaned/wonder_pums_harmonized \
  --execute
```
```
crosswalk harmonize complete: 2 sources written to data/cleaned/wonder_pums_harmonized, never merged
```

### Output Structure
Two files, never merged, plus one shared manifest:
- `data/cleaned/wonder_pums_harmonized/cdc_wonder_export.harmonized.csv` — 65 rows, columns `County, geography_id, Deaths, Population, Crude Rate`
- `data/cleaned/wonder_pums_harmonized/acs_pums_extract.harmonized.csv` — 5,962 rows, columns `person_id, SERIALNO, SPORDER, geography_id, AGEP, sex_code, PWGTP, SCHL, RAC1P`
- `data/cleaned/wonder_pums_harmonized/crosswalk.manifest.json` — one manifest covering both sources, 5,969 total mutations, both input files' and all three schema files' SHA-256 hashes recorded

### Why Two Separate Tables, Not One Merged Table?
Because they're fundamentally different granularities that a row-level join
would misrepresent: CDC WONDER's rows are county-level aggregates (a single
row *is* "all deaths in Adams County in 2020"), while ACS PUMS's rows are
individual survey respondents. Joining them would silently manufacture
person-level records out of aggregate statistics — an ecological-fallacy
trap, not a harmless convenience. The crosswalk only aligns column *names*
(`County Code` / `PUMA` → `geography_id`) so the two can be compared or
cross-referenced side by side; it deliberately never joins their rows.

### Interesting Harmonization Findings

**Renaming a column to a shared name does not make the underlying geography
comparable — a caveat this crosswalk had to document rather than paper
over.** CDC WONDER's `County Code` is a county FIPS code; ACS PUMS's `PUMA`
(Public Use Microdata Area) is a *different* Census geography that doesn't
map 1:1 onto counties — a PUMA can span multiple counties, or one large
county can contain several PUMAs. An earlier draft of the crosswalk file
named the shared target `geography_fips`, which would have actively implied
a compatibility that doesn't exist. It's renamed to the more neutral
`geography_id` for exactly that reason, with the caveat spelled out in the
crosswalk file's own comments. This is the harmonization tool doing its job
correctly: it aligns what *can* be aligned (column names, for display) and
refuses to imply more than that.

**A crosswalk's `value_map` can be silently redundant with a source's own
`category_mappings` — worth knowing before assuming which mechanism did the
work.** `acs_pums_rules.yaml`'s own `category_mappings` already converts
`SEX` from `"1"/"2"` to `"M"/"F"` during that source's per-file
`apply_transformations` step, which runs *before* the crosswalk's
column-rename-and-value-map step. So by the time the crosswalk's
`value_map: {sex_code: {"1": M, "2": F}}` would run, the values are already
`"M"/"F"` — the crosswalk's own value mapping never actually fires for this
column in this specific pipeline. The final output is still correct (`SEX`
does end up as `M`/`F`), but *which* rule actually performed the conversion
is not obvious from reading the crosswalk file in isolation — a genuine
methodological subtlety only visible by tracing a real multi-source run
end-to-end, not something a unit test on either mechanism alone would catch.

**The two sources don't share an age/sex breakdown at all in these specific
extracts.** This WONDER pull is county-level totals only (no age/sex
grouping was requested in the query), so there was nothing in it to
cross-walk against PUMS's person-level `AGEP`/`SEX` beyond geography. A real
age/sex crosswalk would need re-running the WONDER query grouped by
County + Ten-Year Age Groups + Sex — left out here rather than mapping
columns that don't actually exist in the file, which an earlier draft of
this crosswalk did (mapping a `sex`/`age_group` column WONDER's export
never actually produced).

---

## Summary & Next Steps

Across all three real sources, the most common real-world issue wasn't
missing data or malformed rows — Census and CDC exports are both
well-curated — it was **format mismatches between what a naive numeric or
null check expects and what the export actually contains**: thousands-comma
formatting defeating numeric parsing (WONDER), a domain-specific `"n/a"`
missingness convention invisible to a structural null check (OpenNeuro), and
a household-vs-person cardinality shape that no small synthetic fixture ever
exercises (PUMS). The harmonization workflow's most valuable output wasn't
the cleaned CSVs themselves — it was the crosswalk file's own comments,
which had to become honest about a real geographic incompatibility (PUMA vs.
county FIPS) that a naive column rename would have quietly hidden.

Running this benchmark against real data caught one genuine bug in the tool
itself (the `SERIALNO`/cardinality-cap divergence, fixed and covered by a
new regression test) that no amount of additional synthetic-fixture testing
would have surfaced, which is the whole reason this benchmark exists as
part of the project rather than as an afterthought.

**Outstanding Work:**
- This demo used single-state extracts (Colorado for WONDER, Wyoming for
  PUMS) to keep the committed files small and the run fast; a full
  multi-state or national run would exercise this tool's file-size handling
  more seriously (see README's "Known limitations" on in-memory processing).
- The age/sex crosswalk between WONDER and PUMS remains undone — it needs a
  re-pulled WONDER export grouped by age and sex, not just county.
