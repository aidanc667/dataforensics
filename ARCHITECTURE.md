# Architecture & Design Decisions

DataForensics is a Python CLI (`dataforensics`) and a thin read-only Streamlit
viewer (`app.py`) built on one thesis: **most tools submit a one-off cleaning
notebook; this one is a reusable engine that is honest about what it doesn't
know.** Every design decision below exists to serve that thesis — the tool
never adds a convenience feature that quietly undermines it.

## Design principles

These are constraints enforced throughout the codebase, not aspirations.

**Safety & immutability**
- Input files are never opened in write mode. Every `harmonize --execute` run
  computes a SHA-256 of the raw input bytes ([`hashing.py`](src/dataforensics/hashing.py))
  and records it in the manifest.
- Output always goes to an explicit `--output` path; the CLI refuses to run
  if it resolves to an input path.
- Writes are atomic: [`manifest.py`](src/dataforensics/manifest.py) writes to
  a temp file in the destination directory, then `os.replace`s it into place
  — a crash mid-run never leaves a half-written file pretending to be
  complete.
- Rows are never silently deleted and columns are never silently dropped.

**Never guess semantics — detect ≠ know**
- No transformation (type cast, date parse, missing-code substitution,
  category merge) is ever applied without an explicit rule in a user-supplied
  rules YAML. Column names are hints, not facts — `age`, `id`, `date` mean
  nothing to the engine until a schema says so.
- `scan` (read-only) is allowed to *infer and suggest* — candidate sentinel
  codes, ID-like columns, ambiguous date formats, string-similarity category
  clusters ([`investigate.py`](src/dataforensics/investigate.py)) — but every
  suggestion carries its evidence and is never auto-applied.
- IDs (and anything matching an ID/FIPS/ZIP-like naming pattern) default to
  string type and are never numerically cast or outlier-tested.
- Ambiguous dates (`03/04/2024` with no declared format) are flagged, never
  silently parsed one way.

**Three-tier validation, never mixed**
- Every check in [`validation.py`](src/dataforensics/validation.py) is one
  of: **Error** (objectively violates an explicit schema rule), **Warning**
  (suspicious, only fires if a schema rule defines the relevant bound), or
  **Suggestion** (a heuristic — outlier, rare category — never counted as an
  error).
- A statistically unusual value is never automatically "wrong." Outliers and
  rare categories are always suggestions with their detection method named,
  never auto-deleted or imputed.

**Determinism & auditability**
- Same input + same rules file + same tool version → identical output.
- Every mutation is logged with a `row_key` (not a bare row index, which
  isn't stable if row order ever changes), the before/after value, the rule
  that caused it, and a human-readable reason — see
  [`manifest.py`](src/dataforensics/manifest.py).
- [`audit_report.py`](src/dataforensics/audit_report.py) renders a
  self-contained HTML report answering: what did I receive, what deserves
  attention, what changed, did the cleaning damage anything, what's still
  unresolved.

**Privacy defaults**
- Any column matching a name/SSN/MRN/email/phone/DOB-like pattern
  (`is_pii_like_column` in [`typing_guards.py`](src/dataforensics/typing_guards.py))
  is masked — aggregate stats only — in every generated report by default.
- The tool never claims "PII-safe" or "HIPAA-compliant." The honest phrasing
  it uses is "potential identifier pattern detected; values masked in this
  report."
- The Analysis Readiness section never reduces a dataset's trustworthiness to
  a single 0–100 score, and never claims a clean scan means the data is
  scientifically valid — only that no configured-rule violations were found.

## Module map

```
src/dataforensics/
├── cli.py            # scan / harmonize / report subcommands, exit codes
├── ingest.py          # CSV/TSV/JSON/Excel readers; encoding + delimiter
│                       # detection; footer-stripping; format errors
├── typing_guards.py   # ID/PII pattern guards, sentinel classification
├── dictionary.py       # data dictionary: dtype, category, missingness,
│                       # outliers, top-coding, per column
├── investigate.py      # pre-rules heuristic findings: duplicates, category
│                       # clusters, missingness patterns, semantic roles —
│                       # suggestions only, never mutates data
├── validation.py        # three-tier (Error/Warning/Suggestion) rule engine
├── harmonize.py          # rules-driven transform + crosswalk mapping
├── quality_score.py       # deterministic, rule-based quality scoring —
│                          # every sub-score traces to a specific check above
├── audit_report.py         # self-contained HTML investigation report
├── manifest.py               # transformation_manifest.json: hashes,
│                             # versions, per-mutation provenance
├── config_schema.py           # rules YAML validation on load
├── report.py                   # JSON → Markdown rendering
├── hashing.py                   # SHA-256 file hashing
└── viewer.py                     # report-type classification for app.py
```

`app.py` is a read-only Streamlit viewer over the same engine the CLI
uses — no new logic, no write path, no way to trigger a transformation the
CLI itself couldn't.

## CLI surface

```
dataforensics scan <file> [--rules schema.yaml] [--out-dir DIR] [--sheet NAME]
    Read-only. Emits a data dictionary and, with --rules, a validation report.

dataforensics harmonize <file> --rules schema.yaml --output <path> [--execute]
    Without --execute: dry run, writes nothing.
    With --execute: applies rules, writes <path> and <path>.manifest.json
    atomically. Refuses if --output equals an input path.

dataforensics harmonize <file1> <file2> ... --rules-map f1=r1.yaml,f2=r2.yaml \
    --crosswalk crosswalk.yaml --output-dir <dir> [--execute]
    Cross-dataset harmonization: each input is standardized against its own
    rules file, then the crosswalk maps both onto one shared target schema
    — emitted as independently-harmonized tables, never row-merged.

dataforensics report <artifact.json>
    Renders a data_dictionary/validation_report/manifest JSON to Markdown.
```

Exit codes: `0` success, `1` validation errors found, `2` invalid rules/config,
`3` runtime/IO failure (malformed input, permission error).

## Rules YAML shape

```yaml
version: 1
primary_key: [participant_id]
columns:
  age:
    type: integer
    minimum: 0
    maximum: 120        # absence of `maximum` => no Warning ever fires
  visit_date:
    type: date
    format: "%Y-%m-%d"  # absence => ambiguous dates flagged, never parsed
missing_values:
  smoking_status:
    "99": Refused
category_mappings:      # merges require an explicit mapping; never automatic
  sex:
    male: Male
```

## Why not a merge of aggregate and individual data

The real-data benchmark ([`WRITEUP.md`](WRITEUP.md)) harmonizes CDC WONDER
(aggregate cell-count mortality data) and Census ACS PUMS (individual-level
microdata) onto one shared column schema — but never row-joins them. WONDER
and PUMS operate at different units of analysis; merging them row-wise would
be an ecological-fallacy trap. The crosswalk path maps both onto a shared
schema and emits them as two independently-harmonized tables instead.

## Testing strategy

421 tests across four levels: unit (per guard/rule), integration (full
pipeline on a fixture), regression (golden input → expected output,
byte-identical on rerun), and end-to-end (CLI/Streamlit invocation through to
manifest). CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs
`ruff`, `mypy`, and the full suite on every push.

## Explicit non-goals

No automatic imputation. No automatic fuzzy-match deduplication (suggest
only). No automatic unit conversion. No ML anomaly detection. No claims of
statistical or scientific validity — only "no violations of configured rules
detected."
