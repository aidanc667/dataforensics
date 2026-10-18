# DataForensics

[![CI](https://github.com/aidanc667/dataforensics/actions/workflows/ci.yml/badge.svg)](https://github.com/aidanc667/dataforensics/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

**Understand → Investigate → Decide → Clean → Verify → Document**

DataForensics is a research-data quality and investigation tool for analysts working with unfamiliar or messy datasets. It helps answer a simple question before analysis begins:

> **Can I trust this dataset, and do I understand what needs attention?**

Rather than automatically changing anything it considers unusual, DataForensics investigates the data first. It profiles the dataset, identifies potential problems, shows the evidence behind each finding, and lets the analyst decide what should change. Approved transformations are then applied and verified, with every change recorded in an audit trail.

**[Live app](https://dataforensics.streamlit.app/)** · **[Real-data benchmark & findings](WRITEUP.md)** · **[Architecture & design decisions](ARCHITECTURE.md)**

## What it does

DataForensics takes a raw research export and works through six stages:

**1. Understand**
Builds a data dictionary and summarizes the structure of the dataset, including variable types, missingness, unique values, distributions, dates, and potential identifiers.

**2. Investigate**
Looks for potential problems such as duplicate records, inconsistent categories, missing-value codes, ambiguous dates, unusual values, outliers, top-coding, and cross-column inconsistencies.

**3. Decide**
Shows the evidence behind each finding and separates what the system knows from what requires human judgment. Nothing is changed automatically.

**4. Clean**
Applies only transformations that are explicitly defined in a rules file or approved by the analyst.

**5. Verify**
Checks the output against the original dataset to make sure approved changes occurred and unintended changes did not.

**6. Document**
Produces a record of the findings, approved transformations, verification results, and remaining issues so the final dataset can be traced back to the original.

## Why this matters

Cleaning research data is not simply a matter of finding unusual values and fixing them. An unusual observation may be a legitimate participant, a valid measurement, a repeated visit, or an actual data-entry error.

DataForensics therefore treats **detection and correction as separate steps**.

For example, if a dataset contains:

```text
Never
never
NEVER
N
0
```

DataForensics can flag the inconsistent coding and show the affected records, but it does not assume that `N` or `0` means `Never`. The analyst makes that decision.

This makes the cleaning process **traceable, reviewable, and reproducible** rather than a series of silent automated changes.

## Investigation examples

DataForensics can surface findings such as:

* Duplicate participant IDs
* Potential duplicate records
* The same real-world entity recorded under two different IDs — an exact match on identifying fields, or a spelling variant (e.g. "Jon Smith" vs. "John Smith" on the same birth date)
* Missing-value sentinels such as `-99` or `"Refused"`
* Inconsistent categorical values
* Ambiguous date formats and impossible date ordering
* Values outside configured ranges
* Statistical outliers and top-coded distributions
* Values that don't match a column's own dominant format (phone numbers, emails, names, or any other consistently-formatted column)
* A personal identifier (SSN, email, phone) pasted into a column whose name gives no indication it holds PII
* A negative flag column paired with a dependent detail column that should logically be empty (`has_spouse = No` but `spouse_name` is filled in)
* Whitespace padding, invisible/control characters, and encoding corruption (mojibake)
* Numbers written in a different format than the rest of their column (`"$50,000"` vs. plain numbers)
* Two measurement units mixed in one column (kg/lb, cm/in, dollars/thousands)
* An age column whose values look like birth years
* A column that should never exceed another (start/end, min/max, admission/discharge)
* Missing observation-freshness, future-dated values, and irregular coverage gaps in date columns
* Schema and distribution drift against a previously-saved fingerprint (columns added/removed/renamed, value distributions shifting)
* Conflicting values across related variables
* Potential identifier columns
* FIPS, ZIP, and other identifier formatting issues
* Potential referential-integrity problems across files, a candidate join key that isn't actually unique, and field-level value discrepancies between two files sharing a key (record-by-record for a one-to-one relationship, or consistency-across-repeated-keys for a one-to-many relationship)
* An accurate, previewable row-merge of two files on a confirmed key — offered only after the checks above, never automatic
* Round-number heaping (self-reported weights clustering at 150, 160, 200)
* Missing rates that differ by group ("income missing for 36% of people under 35 vs 0% of those 35+")
* Redundant columns, rare category levels, age that disagrees with date of birth, and totals that don't equal the sum of their parts
* Your own expectations — ranges, required, unique, allowed values, and if-then rules for skip logic and impossible combinations
* Spreadsheet damage: IDs turned into scientific notation, dates stored as Excel numbers (45000), and codes that lost their leading zeros
* Synonym codes for one category (M / Male, Y / Yes / True), impossible negatives (negative age, count, price), and unusual combinations of common values
* Repeated records of one person over time where age goes down or birth date/sex changes
* ZIP codes in the wrong state, latitude/longitude out of range or swapped, temperatures mixing °F and °C, distances mixing miles and km
* Date-times mixing time zones or with impossible times; street addresses and titled names in free text; placeholder/junk text; text cut off at a field limit

## Cleaning, verification, and insights

* **51 automated checks** (DF-01 to DF-51), each reported as Flagged, Passed, or Not applicable — a check that needs columns the dataset doesn't have is never shown as "Passed". The app's **Checks** page (sidebar) lists every check, what it looks for, and whether it's fixable on approval or flag-only.
* **Safe fixes on approval:** trim whitespace, remove hidden characters, repair broken encoding, convert `$50,000`/`50k` to plain numbers, restore leading zeros in ZIP/FIPS codes and other IDs, convert Excel date numbers to dates, rewrite dates as YYYY-MM-DD, merge synonym codes, convert separable unit mixes, and remove exact duplicate rows. Each shows a before → after preview of real rows first. **Approve all recommended fixes** ticks every safe fix, duplicate removal, missing-value code and high-confidence spelling merge in one click; date order (MM/DD vs DD/MM) and fuzzy spelling matches are left for you to decide. Nothing changes until you click Apply.
* **Judgment calls on approval, never part of Approve all:** treat outliers (cap to the IQR limits, or set to empty — missing-value codes like 9999 are left alone), mask SSNs, emails and phone numbers found inside text (the audit trail never records the original), and fill missing values with the column's median or most common value (shown before you approve; not offered for ID, personal-data, date or free-text columns). They run after the other fixes — so a kg value converted to lb is no longer an outlier — and every changed cell is logged.
* **Before → after re-check:** after Apply, every check runs again on the cleaned file and the score and check changes are shown side by side.
* **Audit trail:** every changed cell (original value, new value, rule, timestamp) on screen and as CSV; input and output SHA-256 hashes in the audit report.
* **Reusable rules:** download `cleaning_rules.yaml` and re-apply the same cleaning to the next release with `dataforensics harmonize ... --execute`.
* **Insights tab:** the strongest, least obvious patterns as ranked plain-language cards with charts — groups that are several times as likely to have an outcome, group differences, strong relationships, missing-data patterns tied to a group (e.g. income missing far more often for people under 35), how concentrated a total is, dominant values, trends over time, and long-tailed distributions. Each card says why it's useful; the full statistics tables sit underneath. Descriptive and unweighted, with a caveat about chance findings. Every pattern gets a second look before it's shown:
  * **Confounding:** it's re-tested within groups of age and sex (by default; any other column can be added on the tab). One that mostly disappears when like is compared with like — e.g. in the ACS sample, "income is missing far more often for the 4th person in a household", which is really about children — is held back with the reason; one that survives says so ("Holds after accounting for age_years (1.9×)"), and one age only shrinks says "Partly explained by age_years". Each group is compared with *other* people of similar age, never with a baseline it dominates itself.
  * **True by definition:** links between columns that measure the same thing (wages ↔ total income) are held back, but only when the names match *and* the data agrees: one is part of a column named as the total, a ratio/average-style column strongly tracks the other, or one column predicts the other almost perfectly.
  * **Skip patterns vs break-offs:** a question blank for exactly the people a previous answer screens out is labelled a designed skip, and the skip is used to explain that column's other missing-data patterns; if those rows are also blank in many other columns (more than 10%, and more than twice everyone else's rate), they're flagged as a possible break-off instead.
  * **Codebooks:** upload a `column,value,label` CSV and coded values read as "3 (Widowed)". The two real example datasets come with their official ACS/BRFSS codebooks.

  Held-back patterns (up to 12, one per column pair) are listed with their reasons under the cards, so a pattern that fails the second look is shown as held back rather than simply disappearing.
* **Checks tab:** every check as a searchable, filterable card: what it looks for, why it matters, and whether it's fixable on approval or flag-only.

## Measured detection: error-injection benchmark

`dataforensics benchmark` plants known errors in clean synthetic data and measures what's caught. Ground truth comes from the injector, never the detectors. 10 seeds × 1,000 rows:

| Planted error | Recall | Flags on clean items | Left after approved fixes |
|---|---:|---:|---:|
| Missing-value codes (9999) | 100% | 0 | 0 |
| Exact duplicate rows | 100% | 0 | 0 |
| Whitespace | 100% | 0 | 0 |
| Hidden characters | 100% | 0 | 0 |
| Broken encoding | 100% | 0 | 0 |
| Inconsistent categories | 100% | 0 | 0 |
| Numbers written as text | 100% | 0 | 0 |
| Mixed kg/lb units (column) | 100% | 0 | 9 of 10 (overlapping ranges: flagged, not auto-converted) |
| Future dates | 100% | 0 | all (detection only, by design) |
| Outliers (column) | 100% | 0 | all (never deleted, by design) |
| Missingness tied to age | 100% | 0 | all (filled only on approval) |

Mean quality score: clean 100 → with errors 97.0 → after approved fixes 97.4. The planted errors touch only a few percent of the 9,000 cells, so the score starts high; what's left after fixing is mostly missing values (the planted gaps, and codes turned into blanks), which cleaning never fills in by itself. On the clean files, the only check that ever fired was the coverage-gap check (1 of 10 runs), which is built to accept about a 5% chance rate. At 300 rows, recall is still 100%, but one chance missingness-by-group pair appeared across 10 runs and the coverage-gap check fired on 3 of 10 clean files (sparse random visit dates have long lulls by chance). Building the benchmark found and fixed five detector problems, and checking the real demo data found two more (see [`WRITEUP.md`](WRITEUP.md#error-injection-benchmark)).

Every finding includes the evidence used to flag it and clearly distinguishes a **potential issue** from a confirmed error.

## Research-oriented safeguards

The tool is intentionally conservative.

* **No automatic deletion of outliers** (exact duplicate rows can be removed, but only when you approve it, and each removed row is re-verified as identical first)
* **No automatic imputation**
* **No automatic fuzzy deduplication** — a fuzzy near-duplicate entity is always a suggestion, never auto-merged
* **No automatic unit conversion** — a kg/lb mix is converted only on approval, and only when the two units' ranges are cleanly separated; overlapping mixes are flagged but never converted
* **No silent category remapping**
* **No merging of separate datasets without explicit review and approval** — the Multi-File Relationships tab's merge step shows a full preview (row counts, matched/unmatched, which columns disagree) before producing anything
* **No claims that a dataset is scientifically valid simply because checks pass**

If DataForensics cannot determine what a value means from the available evidence, it reports the uncertainty rather than guessing.

## From raw data to analysis-ready data

```text
Raw research export
        ↓
   Understand
        ↓
   Investigate
        ↓
      Decide
        ↓
      Clean
        ↓
     Verify
        ↓
    Document
        ↓
Analysis-ready dataset
```

The output is not just a cleaned file. The analyst receives:

* **Cleaned dataset**
* **Data dictionary**
* **Investigation findings**
* **Approved transformation log**
* **Verification results**
* **Remaining issues requiring review**
* **Audit report**

## Interactive app

The Streamlit application provides a visual interface to the same engine used by the CLI.

Upload a CSV, TSV, JSON, or Excel file and DataForensics produces a dataset investigation covering:

* Dataset structure and variable roles
* Missingness and distributions
* Potential data-quality issues
* Evidence for each finding
* Suggested actions
* Cross-column checks
* Optional Survey, Clinical & Research, and Geographic profiles
* Before/after transformation review
* Dataset fingerprints for tracking schema and distribution drift between versions
* Multi-file relationship, referential-integrity, and value-reconciliation checks, plus an approval-gated row-merge with a full before-you-commit preview

After reviewing the findings, approve individual transformations and export the cleaned dataset together with its audit documentation.

No file on hand? The app includes three example datasets, no upload needed. Two are genuine, unmodified subsamples of public U.S. government microdata, each chosen for a specific, well-documented real-world messiness pattern: [ACS PUMS](https://www.census.gov/programs-surveys/acs/microdata.html) (Census — genuine income top-coding, skip-pattern missingness) and [BRFSS](https://www.cdc.gov/brfss/annual_data/annual_2023.html) (CDC — textbook missing-value sentinel codes, age top-coding). The third, **Messy CSV Example**, is synthetic — a deliberately messy intake-form CSV built to exercise the checks that pre-coded, dateless government microdata structurally never can (category-spelling standardization, ambiguous dates, near-duplicate entities, unit-mixing), and the only one of the three that actually needs approved changes in Review & Approve. See [`fixtures/demos/README.md`](fixtures/demos/README.md) for exact provenance, column mappings, and the full list of what's planted in the synthetic example.

## Quickstart

Clone the repository and install the package:

```bash
pip install -e ".[dev]"
```

Run the bundled example:

```bash
dataforensics scan fixtures/sample.csv --rules fixtures/sample_rules.yaml
```

Preview proposed transformations:

```bash
dataforensics harmonize fixtures/sample.csv \
  --rules fixtures/sample_rules.yaml \
  --output /tmp/out.csv
```

Nothing is changed during a dry run. To apply the approved rules:

```bash
dataforensics harmonize fixtures/sample.csv \
  --rules fixtures/sample_rules.yaml \
  --output /tmp/out.csv \
  --execute
```

The command writes the cleaned dataset and a transformation manifest.

## Interactive application

Install the optional viewer dependencies:

```bash
pip install -e ".[dev,viewer]"
streamlit run app.py
```

## Supported inputs

* CSV
* TSV
* JSON containing a top-level array of flat objects
* Excel (`.xlsx`, `.xls`)

For multi-sheet Excel workbooks, the sheet must be explicitly selected rather than guessed.

## Testing

The project includes unit, integration, regression, and end-to-end tests.

Run the full suite:

```bash
pytest
```

The bundled fixture contains intentionally planted issues used to test the complete workflow.

The project has also been tested against real public datasets, including:

* CDC WONDER mortality data
* ACS PUMS microdata
* OpenNeuro participant data

The real-data benchmark is documented in [`WRITEUP.md`](WRITEUP.md).

## What DataForensics does not try to do

DataForensics is not intended to replace established validation frameworks or perform the entire analysis workflow.

It does not:

* determine whether a research hypothesis is correct
* establish scientific or causal validity
* automatically decide whether an unusual observation is erroneous
* infer undocumented study-specific meanings
* replace domain expertise

Its job is narrower:

> **Help an analyst understand an unfamiliar dataset, identify what deserves attention, make controlled changes, and preserve a record of what happened.**

## Documentation

For the detailed architecture, safety invariants, data schemas, transformation rules, and implementation decisions, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

For the real-dataset benchmark, the error-injection benchmark, and findings, see [`WRITEUP.md`](WRITEUP.md).
