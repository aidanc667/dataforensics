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
* Missing-value sentinels such as `-99` or `"Refused"`
* Inconsistent categorical values
* Ambiguous date formats
* Values outside configured ranges
* Statistical outliers
* Top-coded distributions
* Conflicting values across related variables
* Potential identifier columns
* FIPS, ZIP, and other identifier formatting issues
* Potential referential-integrity problems across files

Every finding includes the evidence used to flag it and clearly distinguishes a **potential issue** from a confirmed error.

## Research-oriented safeguards

The tool is intentionally conservative.

* **No automatic deletion of outliers**
* **No automatic imputation**
* **No automatic fuzzy deduplication**
* **No automatic unit conversion**
* **No silent category remapping**
* **No row-level merging of separate datasets**
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
* Dataset fingerprints for tracking changes between versions
* Multi-file relationship and referential-integrity checks

After reviewing the findings, approve individual transformations and export the cleaned dataset together with its audit documentation.

No file on hand? The app includes three real example datasets, no upload needed — genuine, unmodified subsamples of public U.S. government microdata, each chosen for a specific, well-documented real-world messiness pattern: [ACS PUMS](https://www.census.gov/programs-surveys/acs/microdata.html) (Census — genuine income top-coding, skip-pattern missingness), [BRFSS](https://www.cdc.gov/brfss/annual_data/annual_2023.html) (CDC — textbook missing-value sentinel codes, age top-coding), and [NHANES](https://wwwn.cdc.gov/nchs/nhanes/continuousnhanes/default.aspx?Cycle=2021-2023) (CDC/NCHS — clinical exam data with real skip-pattern missingness). See [`fixtures/demos/README.md`](fixtures/demos/README.md) for exact provenance and column mappings.

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

For the real-dataset benchmark and findings, see [`WRITEUP.md`](WRITEUP.md).
