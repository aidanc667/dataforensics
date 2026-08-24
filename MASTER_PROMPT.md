# Master build prompt: research-data-harmonizer (`rdh`)

Paste this whole document as the opening message to a fresh Claude Code session, pointed at this (empty, git-initialized) directory. It is the complete spec — do not ask the user to re-explain scope; everything you need is here. If something genuinely isn't decided below, stop and ask rather than guessing.

## 0. What this is and why it exists

A reusable Python CLI/package that takes messy real-world research tabular exports and produces: a data dictionary, a tiered validation report, and — only when a user supplies an explicit rules file — a standardized/cleaned output plus a full audit manifest. It is built to be run against three genuinely different real public datasets (CDC WONDER, Census ACS PUMS, OpenNeuro `participants.tsv`), including one genuine cross-dataset harmonization demo (§3).

This is a portfolio project. Its entire value proposition is: **most people submit a one-off cleaning notebook; this is a reusable tool that is honest about what it doesn't know.** Every design decision below exists to serve that pitch — do not add convenience features that quietly undermine it (auto-inference, auto-deletion, auto-conversion). When in doubt, the tool preserves data and reports uncertainty instead of guessing. That sentence is the actual product.

## 1. Non-negotiable design principles

Organize your mental model around these five clusters. They are constraints on every module you write, not just the ones that sound relevant.

**Safety & immutability**
- Input files are never opened in write mode, ever. Every run hashes the raw input bytes (SHA-256) and asserts the hash is unchanged before writing any output.
- Output must go to an explicit `--output` path; refuse to run if it resolves to the same path as any input.
- Writes are atomic: write to a temp file, validate it fully wrote, then rename into place. A crash mid-run must never leave a half-written output pretending to be complete.
- Rows are never silently deleted and columns are never silently dropped. `rows_in == rows_out` and `cols_in == cols_out` are asserted after every run unless a rule explicitly removed something — if so, the manifest records the rule, the reason, and the exact count. Unexpected row/column loss is a hard failure, not a log line.

**Never guess semantics — detect ≠ know**
- No transformation (type cast, date parse, unit conversion, missing-code substitution, category merge) is ever applied without an explicit rule in the user-supplied rules YAML. Column names are hints, not facts — `age`, `weight`, `id`, `date` mean nothing to the engine until a schema says so.
- `scan` (read-only) is allowed to *infer and suggest* — candidate sentinel codes, ID-like columns, ambiguous date formats, string-similarity category clusters — but every such suggestion is labeled `inference confidence: low — review required` in the report and never auto-applied.
- IDs (and anything matching an ID/FIPS/ZIP/GEOID naming pattern, or with preserved-length string values) default to string type, are never numerically cast, and are never outlier-tested.
- Ambiguous dates (`03/04/2024` with no ISO8601 and no explicit `--date-format`/schema format) are flagged as a Critical validation warning and never silently parsed one way.
- Units are never inferred or converted unless the schema explicitly states source and target units and the conversion factor; the manifest records the exact formula and rows affected.
- `harmonize` requires `--execute` to write anything; without it, it's a dry run that prints the proposed transformation list (rule → rows affected) and writes nothing.

**Three-tier validation, never mixed**
- Every check is one of: **Error** (objectively violates an explicit schema rule — e.g. `age < 0`, or a duplicate value in a declared primary key), **Warning** (suspicious but may be valid, and only fires if a schema rule defines the relevant bound — e.g. `age > 120` if `age.maximum: 120` is configured; with no schema rule, this is not evaluated at all), or **Suggestion** (a heuristic — IQR/MAD outlier, rare category, high-confidence string-similarity category merge candidate — never counted as an error, always labeled with the method used).
- Every reported check has one of four states: PASSED, WARNING, FAILED, or **NOT EVALUATED**. Never omit a check silently or imply something was checked when no rule/metadata existed to check it against.
- Never produce a single aggregate "quality score." Report counts by category instead (`Hard errors: 2, Warnings: 14, Suggestions: 6, Rules evaluated: 24, Rules passed: 22`).
- A statistically unusual value is never automatically "wrong." IQR/MAD outlier flags, rare categories, and extreme-but-plausible values are always Suggestions, never Errors, and are never auto-deleted, winsorized, capped, or imputed.

**Determinism, reproducibility, and versioning**
- Same input + same rules file + same tool version ⇒ byte-identical output, every time. No unseeded randomness anywhere in profiling or transformation.
- Transformations must be idempotent: running `harmonize` twice on the same output must not change it further. Write a test for this specifically.
- Every manifest carries: `tool_version`, `python_version`, resolved dependency versions, `schema_sha256` (hash of the rules file), `input_sha256` (hash of raw input bytes, not filename), `run_id`, `timestamp_utc`, and an optional user-supplied (never inferred) `provenance:` block (source org, dataset, release, download date).
- Pin dependencies with a lockfile so the recorded dependency versions in the manifest are actually reproducible from a fresh clone.

**Privacy defaults**
- Any column matching a name/SSN/MRN/email/phone/DOB-like pattern is masked (aggregate stats and counts only) in every generated report by default; raw value samples are opt-in via an explicit flag.
- Never claim "PII-safe" or "HIPAA-compliant" — the honest phrase is "potential identifier pattern detected; values masked in this report."
- Sensitive values never enter the manifest itself — reference by row index/count/hash, not by value.
- Note for the README: the three demo datasets used here (CDC WONDER aggregate extracts, Census ACS PUMS microdata, OpenNeuro `participants.tsv`) are already public, de-identified research releases — none contain real PHI. The privacy machinery above is a capability demonstration for what this tool would do against a real REDCap/EHR export, not a claim that these specific demo files needed it.

## 2. Blind spots this spec did not originally cover — build these in too

- **CDC WONDER's small-cell suppression is its own category, not "missing."** WONDER redacts counts below a threshold (commonly shown as `Suppressed` or blank per its confidentiality policy) — treat this as a distinct declared sentinel with its own label, separate from `NaN` and from user-declared missing codes like `-99`. Don't let it get silently coerced to null by a generic numeric parser.
- **The three datasets each have a genuinely different missing-value/metadata convention — make this explicit in the write-up, it's a real differentiator:** CDC WONDER's suppression + disclaimer footer; ACS PUMS's numeric top-codes plus a *separate* data dictionary file the Census Bureau ships (a real test of the "metadata is data too" principle — ingest and validate against it, don't just profile the CSV blind); OpenNeuro/BIDS's `"n/a"` string convention plus JSON sidecar files describing each column. Three different real conventions, one engine, driven entirely by config — that's the actual thesis of the project, say so explicitly in the README.
- **Do not literally join/merge CDC WONDER rows with ACS PUMS rows.** WONDER (in most of its tables) is aggregate cell-count data (e.g. deaths by county × age band × sex); PUMS is individual/household-level microdata. Row-wise merging aggregate and individual data is methodologically wrong (an ecological-fallacy trap) and would undermine the project's credibility with anyone who understands the data. The harmonization demo must map both sources onto **one shared column schema and coding** (e.g. `age_band`, `sex`, `geography_fips`) and emit them as two independently-harmonized tables under that shared schema — never a merged/joined single table. State this scope decision explicitly in the README so it reads as a deliberate methodological choice, not an oversight.
- **The rules YAML itself needs a defined, versioned format and validation on load** — a malformed rules file must fail fast with exit code 2 and a specific error, not partially apply.
- **A working "5-minute quickstart" needs a bundled tiny synthetic fixture** (a 20-row CSV with a couple of planted issues) shipped in the repo, so a reviewer can run `rdh scan` and `rdh harmonize --dry-run` immediately without first downloading real government data.
- **Soften "audit log as legal record" to "audit trail" in all user-facing docs.** Keep every field the original ask specified (who/what/when/why for every mutation) — just don't imply actual legal admissibility, which the tool can't guarantee and shouldn't claim.
- **This project has grown well beyond a weekend script.** Build it in the phased order in §7 so it's demoable at every checkpoint — never let it sit half-wired across every module at once.

## 3. Datasets

| Source | What to pull | Real-world quirks to expect (test these) |
|---|---|---|
| [CDC WONDER](https://wonder.cdc.gov/) | One export from any public database (e.g. Underlying Cause of Death) as TSV | Query-parameter/disclaimer footer block at file tail; small-cell suppression; possible inconsistent date grain |
| [Census ACS PUMS](https://www.census.gov/programs-surveys/acs/microdata.html) | One state-level person or household PUMS extract + its accompanying data dictionary file | Numeric top-coding at privacy thresholds; PUMA/FIPS geography codes with leading zeros; a real separate codebook to ingest |
| [OpenNeuro](https://openneuro.org/) | `participants.tsv` + a phenotype file from any BIDS dataset | BIDS `"n/a"` missing convention; JSON sidecar metadata; free-text fields; small-N cardinality |

Harmonization demo (§1, "never literally join"): CDC WONDER + ACS PUMS mapped onto a shared `{geography_fips, age_band, sex}` schema, emitted as two independently-harmonized tables. OpenNeuro runs solo through `scan`/`harmonize` as the third proof case — no forced join.

## 4. Repo structure

```
research-data-harmonizer/
├── pyproject.toml              # packaging + console_scripts entry: rdh
├── README.md                   # pitch, quickstart, differentiation vs. ydata-profiling/great_expectations, explicit non-goals
├── WRITEUP.md                  # 1-page before/after per dataset, specific issues caught
├── src/rdh/
│   ├── cli.py                  # scan / harmonize / report subcommands
│   ├── ingest.py                # encoding/dialect detection, footer/disclaimer stripping
│   ├── typing_guards.py         # ID/FIPS/ZIP string guard, sentinel handling
│   ├── dictionary.py            # data dictionary generation
│   ├── validation.py            # three-tier validation engine (schema / integrity / heuristic)
│   ├── harmonize.py             # rules-driven transform engine + crosswalk mapping
│   ├── manifest.py              # transformation_manifest.json writer, versioning/hash fields
│   ├── report.py                 # JSON -> Markdown rendering
│   └── config_schema.py         # rules YAML validation
├── schemas/                     # example rules YAML per dataset + the WONDER/PUMS crosswalk
├── fixtures/                    # tiny synthetic CSVs with planted issues, for tests + quickstart
├── data/
│   ├── raw/                     # downloaded real extracts, never modified, gitignored if large
│   ├── standardized/             # lossless fixes only (encoding/footer/dtype-guard, no semantics)
│   └── cleaned/                  # rule-driven harmonize --execute output
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/               # golden input -> expected output pairs
│   └── e2e/
└── .github/workflows/ci.yml     # pytest on push (nice-to-have, keep minimal)
```

## 5. CLI specification

```
rdh scan <file> [--config schema.yaml]
    Read-only. Writes data_dictionary.json/.md and validation_report.json/.md.
    Never writes to the input path or any transformed data file.

rdh harmonize <file...> --rules schema.yaml --output <path> [--execute]
    Without --execute: dry run. Prints proposed transformations (rule -> rows affected), writes nothing.
    With --execute: applies rules, writes <path> and <path>.manifest.json atomically.
    Refuses if --output equals any input path.
    Accepts 1 file (single-dataset standardization) or 2+ files with a crosswalk rules file (harmonization).

rdh report <artifact.json>
    Renders a data_dictionary/validation_report/manifest JSON file to Markdown.
```

Exit codes: `0` success/no hard errors, `1` validation errors found, `2` invalid rules/config, `3` runtime/IO failure.

## 6. Rules YAML shape (illustrative — finalize exact keys in `config_schema.py`, validate on load)

```yaml
version: 1
primary_key: [participant_id]            # or composite for longitudinal data, e.g. [participant_id, visit_date]
columns:
  age:
    type: integer
    minimum: 0
    maximum: 120                          # absence of `maximum` => no Warning ever fires for high age
  participant_id:
    type: id                              # forces string, exempts from outlier testing
  visit_date:
    type: date
    format: "%Y-%m-%d"                    # absence => ambiguous dates flagged, never parsed
missing_values:
  bmi: [-9]
  smoking_status:
    "99": Refused
category_mappings:                        # merges require an explicit mapping; never automatic
  sex:
    M: Male
    male: Male
weights_strata:                           # recognized only via config; excluded from auto-cleaning/outlier logic
  columns: [survey_weight, strata, psu]
```

For the WONDER/PUMS harmonization, an additional crosswalk file maps each source's rules-YAML output columns onto the shared target schema (`age_band`, `sex`, `geography_fips`) — same manifest logging as any other rule.

## 7. Build order (phased — keep it demoable at every step)

1. **Skeleton**: packaging, `rdh` entry point, empty subcommands that print "not implemented," CI running an empty test suite. Commit.
2. **Ingest + dictionary**: encoding/dialect detection, footer-stripping, ID/FIPS guard, `rdh scan` producing a data dictionary (no validation yet) against the fixtures. Commit + unit tests.
3. **Validation engine**: three-tier model, PASSED/WARNING/FAILED/NOT EVALUATED states, rules YAML parsing + validation-on-load. `rdh scan` now emits the full validation report. Commit + unit tests including the false-positive cases (age=100, rare category must NOT error).
4. **Harmonize (single file)**: dry-run printing, `--execute`, atomic writes, manifest with full versioning/hash block, idempotency test. Commit + regression tests (golden files).
5. **Cross-dataset crosswalk**: WONDER + PUMS mapped to shared schema, emitted as two harmonized tables, not merged. Commit + a dedicated test on a synthetic fixture proving no row-level merge occurred.
6. **Real datasets end-to-end**: run all three real sources through `scan`, run the crosswalk demo, capture before/after output for the write-up. Fix whatever real ingestion breaks (there will be something — that's the point).
7. **Polish**: README (pitch + quickstart + explicit non-goals list from §1/§2), `WRITEUP.md`, exit-code/failure-mode tests, final full test-suite pass.

## 8. Testing strategy

Four levels: unit (per guard/rule), integration (full pipeline on one fixture), regression (golden input→output pairs, byte-identical on rerun), end-to-end (CLI invocation → report files → cleaned output → manifest). Plus: a synthetic benchmark with deliberately planted errors (measure recall) *and* deliberately valid-but-unusual rows — age=100, one-off rare category, extreme-but-real lab value — that must score zero false Errors (measure precision on the Error tier specifically). Plus explicit failure-mode tests: missing file, malformed CSV, empty file, duplicate column names, invalid YAML, output-path collision, permission error. Target 25-35 tests total across the four levels — more than the original 10-15 floor, proportionate to the expanded scope.

## 9. Definition of done

- [ ] Raw input file hash identical before/after every run, on all three real datasets
- [ ] `harmonize` without `--execute` never writes a file
- [ ] No transformation occurs without a corresponding rule in the rules YAML
- [ ] Ambiguous dates never silently parsed; units never guessed
- [ ] Rows never silently deleted; row/column counts asserted every run
- [ ] Same input + rules + version ⇒ byte-identical output (tested twice)
- [ ] All planted synthetic errors detected; all planted valid-but-unusual rows NOT flagged as Error
- [ ] Potential identifiers masked in default reports; raw samples opt-in only
- [ ] README states explicit non-goals (§1/§2) and the WONDER/PUMS "no row merge" scope decision
- [ ] Fresh clone → quickstart with bundled fixture works in under 5 minutes

## 10. Explicit non-goals for v1 (state these in the README, don't build them)

No automatic imputation. No automatic fuzzy-match deduplication (suggest only). No automatic unit conversion. No NLU/codebook semantic parsing beyond ingesting an explicitly-provided data dictionary/JSON sidecar. No ML anomaly detection. No GUI. No claims of statistical/scientific validity — only "no violations of configured rules detected."
