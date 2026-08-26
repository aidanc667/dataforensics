# DataForensics (`dataforensics`)

Most research-data cleanup tools profile a file and hope for the best. **DataForensics** is built
around one rule instead: **when it's uncertain, it preserves the data and reports the uncertainty —
it never guesses.** No transformation happens without an explicit rule you wrote down (or
explicitly approved in the app); every one that does happen is logged to an audit trail with
enough detail to answer "exactly what happened to this dataset, and why."

## Why not ydata-profiling / great_expectations / pointblank?

Those are excellent generic profilers. DataForensics is narrower and more opinionated, tuned specifically
to research-export quirks those tools don't target: REDCap-style missing-value sentinels (`-99`,
`"Refused"`) kept distinct from true nulls, FIPS/ZIP/ID columns protected from integer-cast
leading-zero truncation, IQR-based outlier and top-code-spike detection reported as suggestions
(never silently corrected), and BOM/encoding/dialect/footer quirks handled before they corrupt a
naive parser. If you don't need those, use a generic profiler — it'll do less, but it'll also ask
you for less.

## Quickstart (a couple of minutes, no downloads required)

```bash
pip install -e ".[dev]"
dataforensics scan fixtures/sample.csv --rules fixtures/sample_rules.yaml
dataforensics harmonize fixtures/sample.csv --rules fixtures/sample_rules.yaml --output /tmp/out.csv
```

The first command profiles the bundled fixture (writes `sample.data_dictionary.{json,md}` and
`sample.validation_report.{json,md}` into the current directory) and reports its planted issues:
a duplicate participant ID, a below-minimum (negative) age, an ambiguous `visit_date` value
(`03/04/2024` — MM/DD or DD/MM? — flagged rather than silently guessed), and several IQR-outlier
suggestions — exit code `1` because errors were found. The second command previews — without
writing anything —
what the same rules file would change (a `missing_values` sentinel remap on `smoking_status`). Add
`--execute` to actually write `/tmp/out.csv` plus `/tmp/out.csv.manifest.json`.

## Interactive app (optional)

What started as a read-only JSON viewer grew into a full interactive front end over the same
engine the CLI uses — same functions, same tested safety guarantees, no parallel logic.

```bash
pip install -e ".[dev,viewer]"
streamlit run app.py
```

Three tabs:

- **Analyze & Clean** — upload a CSV/TSV/JSON/Excel file (or click "Use bundled example"), and
  it runs the full
  Upload → Investigate → Review & Approve → Cleaned Dataset workflow: data dictionary, a
  findings-severity dashboard, suggested variable roles, dataset fingerprinting (download today's
  fingerprint, upload a prior one next time to see exactly what changed), duplicate-row/sentinel/
  ambiguous-date/near-duplicate-category findings you approve individually, and — once you apply —
  a full deliverable bundle: cleaned CSV, `provenance.json`, `validation_results.json`,
  `data_dictionary.html`, `quality_report.html`, `audit_report.md`.
- **Multi-File Relationships** — upload 2+ files from the same study; it suggests shared key
  columns by name *and* real value overlap, and checks referential integrity across a pair you
  pick. Discovery only — nothing is ever joined or merged.
- **Report Viewer** — the original read-only mode: paste in a `data_dictionary`/
  `validation_report`/manifest JSON artifact `scan`/`harmonize --execute` already produced
  elsewhere, and see it rendered.

## CLI reference (as actually implemented today)

```
dataforensics scan <file> [--rules schema.yaml] [--out-dir DIR] [--sheet NAME]
    Read-only. Accepts CSV, TSV, JSON (a top-level array of flat objects), or Excel
    (.xlsx/.xls). --sheet picks a sheet in a multi-sheet Excel workbook (required if the
    workbook has more than one -- scan refuses rather than guessing which one you meant);
    ignored for non-Excel input. Always writes <stem>.data_dictionary.{json,md}. If --rules
    is given, also writes <stem>.validation_report.{json,md}. Never writes to the input path.
    Exit 0 (clean or no --rules), 1 (validation errors found), 2 (malformed rules file),
    3 (malformed input file -- a duplicate header column, invalid JSON shape, or an
    unresolved multi-sheet Excel workbook).

dataforensics harmonize <file> --rules schema.yaml --output <path> [--execute] [--sheet NAME]
    Single-file mode. Accepts CSV, TSV, JSON, or Excel input the same way scan does; --sheet
    picks a sheet in a multi-sheet Excel workbook. Without --execute: dry run, writes nothing,
    just lists proposed transformations (footer-stripping warnings, if any, are printed on the
    dry run too, not just --execute). With --execute: applies the rules, writes <path> and
    <path>.manifest.json atomically. Refuses (exit 2) if --output equals the input path
    or if the rules file is malformed; exits 3 on a malformed input file or if a post-transform
    safety check fails (refuses to write rather than risk silent data loss).

dataforensics harmonize <file1> <file2> [...] --rules-map file1=schema1.yaml,file2=schema2.yaml \
    --crosswalk crosswalk.yaml --output-dir <dir> [--execute]
    Cross-dataset mode (2+ files). Each source is validated/standardized against its OWN
    rules file first, then the crosswalk file remaps each source's columns onto a shared
    target schema. Writes one file per source into --output-dir
    (<output-dir>/<source-stem>.harmonized.csv) plus one crosswalk.manifest.json — sources
    are NEVER row-joined or merged into one table. Requires all three of --rules-map,
    --crosswalk, and --output-dir; falls back to single-file mode only when exactly one
    file and --rules/--output are given. Exits 2 if --output-dir collides with an input
    path, a source has no --rules-map entry, or a source's filename stem has no matching
    entry under the crosswalk file's `sources:` key; exits 3 on a malformed input file
    (including an unresolved multi-sheet Excel source -- --sheet is not available in
    crosswalk mode, so a multi-sheet source must be split or converted to a single-sheet
    file first) or a failed safety check for any source (nothing is written for ANY source
    in that case — see the two-pass validate-then-write design below).

dataforensics report <artifact.json> [--out <path>]
    Renders a data_dictionary/validation_report/manifest JSON artifact (the same JSON
    `scan`/`harmonize --execute` already write to disk) to Markdown. The artifact type is
    auto-detected from its shape (manifest: has `mutations` + `run_id`; validation_report:
    has `errors`/`warnings`/`suggestions`; data_dictionary: a mapping of column name ->
    per-column profile dict) and titled accordingly. Without --out, the Markdown is printed
    to stdout; with --out, it's written to that path instead. Exits 3 on malformed/unreadable
    JSON or on syntactically-valid JSON whose top level isn't an object (e.g. a bare list or
    `null`), 0 otherwise.
```

Exit codes: `0` success/no hard errors, `1` validation errors found, `2` invalid rules/config/
usage, `3` malformed/unreadable input (duplicate-header CSV, unparseable/unreadable JSON artifact
for `report`, invalid-shape JSON input, an unresolved multi-sheet Excel workbook, or a corrupt/
unreadable Excel workbook) or a harmonize safety-check failure (refusing to write rather than risk
silent data loss).

## What this doesn't do (on purpose)

No automatic imputation. No automatic fuzzy-match deduplication (suggestions only, never applied).
No automatic unit conversion. No NLU/codebook semantic parsing beyond an explicitly-provided data
dictionary. No ML anomaly detection. No claims about statistical or scientific validity — only "no
violations of configured rules detected." No row-level merging of cross-dataset sources: the
crosswalk harmonize path aligns column schemas across sources, it never joins them into one table.

The design spec's non-goals list permits exactly one exception: a Streamlit app that calls the
same engine functions the CLI does (no parallel/duplicated logic) — see "Interactive app
(optional)" above.

## Known limitations

**Footer detection is not CSV-quote-aware, and a misclassification truncates the rest of the
file — not just one row.** `strip_footer` (used by every parse path — `scan`, `harmonize`, and the
crosswalk mode) decides whether a line is a footer by counting delimiter characters on the raw line
(`line.count(delimiter)`), not by running a real CSV-quoting parser. A genuine data row containing a
quoted delimiter — e.g. a comma-delimited file with a value like `"Delta Clinic, North"` — has a
higher raw comma count than the header and can be misclassified as a footer line. This is a
plausible trigger, not a rare edge case: two consecutive rows with a quoted delimiter in any
free-text column (site names, addresses, free-text notes) is enough to set it off. And the impact is
larger than a single row: once `strip_footer` detects a run of field-count mismatches, it treats
EVERYTHING from that point to the end of the file as footer and drops it all — this is a
truncation of the dataset, not a single-row exclusion. On a file where the mismatch starts early,
the majority of the rows can be silently dropped. Properly fixing this would mean rewriting the
parser to be CSV-quote-aware (e.g. using Python's `csv` module instead of hand-rolled
`.split(delimiter)`), which is a larger change than this tool currently makes. As a mitigation,
`scan` and `harmonize` both print a stderr warning naming how many lines were dropped and at which
line the drop starts, whenever `strip_footer` actually discards anything, and `harmonize --execute`
also records the count in `*.manifest.json`'s `stripped_footer_lines` field — so a truncation like
this is never silent, even though it isn't automatically prevented. Note that this warning is not
exclusively a failure signal: it also fires on legitimately-footered real exports (e.g. a genuine
CDC WONDER disclaimer/"Query Parameters:" block), which is the intended, correct case for this
heuristic. Either way, if you see this warning, it's worth a quick look — check the row count
against what you expect, and if it's off, check the input file for a data row with a quoted
delimiter near the line named in the warning.

**Excel's own type coercion can destroy information before this tool ever sees the file.**
A spreadsheet cell typed as `007` (e.g. a FIPS or ZIP code) is stored by Excel as the number
`7` — the leading zero is gone before `openpyxl`/`xlrd` read it, and there is no way to
recover it afterward. This is a limitation of the Excel file format itself, not something
`dataforensics`'s `.xlsx`/`.xls` reader can detect or fix. If leading zeros matter, prefer a
CSV/TSV/JSON export of the same data, where the value is preserved as literal text.

## Project status

The core engine — ingest, data dictionary, three-tier validation, single-file harmonize, and
cross-dataset crosswalk harmonize — is implemented and covered by unit, integration, regression,
and end-to-end tests, all passing against the bundled synthetic fixture (`fixtures/sample.csv`).

Running the real-dataset benchmark (CDC WONDER mortality data, ACS PUMS microdata, and an
OpenNeuro participants file) is still outstanding: schema templates for all three sources exist
under `schemas/`, and `WRITEUP.md` is a scaffold with the exact commands to run, but the raw files
have not yet been downloaded and scanned. `WRITEUP.md` says so explicitly rather than presenting
placeholder findings as real ones.

## Full design spec

See [MASTER_PROMPT.md](MASTER_PROMPT.md) for the complete architecture, safety invariants, and
the reasoning behind them.
