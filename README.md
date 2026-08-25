# research-data-harmonizer (`rdh`)

Most research-data cleanup tools profile a file and hope for the best. `rdh` is built around
one rule instead: **when it's uncertain, it preserves the data and reports the uncertainty —
it never guesses.** No transformation happens without an explicit rule you wrote down; every
one that does happen is logged to an audit trail with enough detail to answer "exactly what
happened to this dataset, and why."

## Why not ydata-profiling / great_expectations / pointblank?

Those are excellent generic profilers. `rdh` is narrower and more opinionated, tuned specifically
to research-export quirks those tools don't target: REDCap-style missing-value sentinels (`-99`,
`"Refused"`) kept distinct from true nulls, FIPS/ZIP/ID columns protected from integer-cast
leading-zero truncation, IQR-based outlier and top-code-spike detection reported as suggestions
(never silently corrected), and BOM/encoding/dialect/footer quirks handled before they corrupt a
naive parser. If you don't need those, use a generic profiler — it'll do less, but it'll also ask
you for less.

## Quickstart (a couple of minutes, no downloads required)

```bash
pip install -e ".[dev]"
rdh scan fixtures/sample.csv --rules fixtures/sample_rules.yaml
rdh harmonize fixtures/sample.csv --rules fixtures/sample_rules.yaml --output /tmp/out.csv
```

The first command profiles the bundled fixture (writes `sample.data_dictionary.{json,md}` and
`sample.validation_report.{json,md}` into the current directory) and reports its planted issues:
a duplicate participant ID, a below-minimum (negative) age, and several IQR-outlier suggestions —
exit code `1` because errors were found. The second command previews — without writing anything —
what the same rules file would change (a `missing_values` sentinel remap on `smoking_status`). Add
`--execute` to actually write `/tmp/out.csv` plus `/tmp/out.csv.manifest.json`.

## CLI reference (as actually implemented today)

```
rdh scan <file> [--rules schema.yaml] [--out-dir DIR]
    Read-only. Always writes <stem>.data_dictionary.{json,md}. If --rules is given, also
    writes <stem>.validation_report.{json,md}. Never writes to the input path.
    Exit 0 (clean or no --rules), 1 (validation errors found), 2 (malformed rules file).

rdh harmonize <file> --rules schema.yaml --output <path> [--execute]
    Single-file mode. Without --execute: dry run, writes nothing, just lists proposed
    transformations. With --execute: applies the rules, writes <path> and
    <path>.manifest.json atomically. Refuses (exit 2) if --output equals the input path
    or if the rules file is malformed.

rdh harmonize <file1> <file2> [...] --rules-map file1=schema1.yaml,file2=schema2.yaml \
    --crosswalk crosswalk.yaml --output-dir <dir> [--execute]
    Cross-dataset mode (2+ files). Each source is validated/standardized against its OWN
    rules file first, then the crosswalk file remaps each source's columns onto a shared
    target schema. Writes one file per source into --output-dir
    (<output-dir>/<source-stem>.harmonized.csv) plus one crosswalk.manifest.json — sources
    are NEVER row-joined or merged into one table. Requires all three of --rules-map,
    --crosswalk, and --output-dir; falls back to single-file mode only when exactly one
    file and --rules/--output are given. Exits 2 if --output-dir collides with an input
    path, a source has no --rules-map entry, or a source's filename stem has no matching
    entry under the crosswalk file's `sources:` key.

rdh report <artifact.json>
    Not yet implemented — currently a stub that prints "report: not implemented" and
    exits 3. (Markdown rendering already exists internally and is used by `scan` itself;
    exposing it as a standalone command over an arbitrary artifact file is still open work.)
```

Exit codes: `0` success/no hard errors, `1` validation errors found, `2` invalid rules/config/
usage, `3` runtime/unimplemented.

## What this doesn't do (on purpose)

No automatic imputation. No automatic fuzzy-match deduplication (suggestions only, never applied).
No automatic unit conversion. No NLU/codebook semantic parsing beyond an explicitly-provided data
dictionary. No ML anomaly detection. No claims about statistical or scientific validity — only "no
violations of configured rules detected." No row-level merging of cross-dataset sources: the
crosswalk harmonize path aligns column schemas across sources, it never joins them into one table.

The design spec's non-goals list permits exactly one exception: a thin, read-only Streamlit viewer
over JSON the CLI already produces (no write path, no new engine logic). That viewer is planned,
not yet built.

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
