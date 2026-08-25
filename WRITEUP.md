# DataForensics: Before/After Analysis

**Status:** Template/Scaffold — awaiting real data acquisition and scan execution (Task 17, Steps 1-7)

This document captures the harmonization workflow on three real datasets: CDC WONDER mortality data, ACS PUMS microdata, and an OpenNeuro participants file. Once the raw data are downloaded and processed through `dataforensics scan` and `dataforensics harmonize` in Task 17 Steps 6-7, this scaffold will be filled with real findings and output examples.

---

## Dataset 1: CDC WONDER Mortality Export

### Raw Data Summary
- **Source:** CDC WONDER (https://wonder.cdc.gov/)
- **Dataset:** [TODO: fill in actual query details once downloaded]
- **File:** `data/raw/cdc_wonder_export.tsv`
- **Rows (approx.):** [TODO: insert row count from scan output]
- **Grouping Columns:** [TODO: list actual grouping columns used in export, e.g., county, age_group, sex]

### Scan Results
```
[TODO: Insert full output from: dataforensics scan data/raw/cdc_wonder_export.tsv --rules schemas/cdc_wonder_rules.yaml]
```

**Summary:**
- **Total Issues Found:** [TODO: count of errors, warnings, suggestions]
- **Data Completeness:** [TODO: % non-null rows and key statistics]
- **Most Interesting Finding:** [TODO: describe one notable data quality issue or anomaly caught by the scan]

---

## Dataset 2: ACS PUMS Microdata Extract

### Raw Data Summary
- **Source:** U.S. Census Bureau, American Community Survey (https://www.census.gov/programs-surveys/acs/microdata.html)
- **Extract Details:** [TODO: fill in actual year, state/region, and person-level sample size once downloaded]
- **File:** `data/raw/acs_pums_extract.csv`
- **Rows (approx.):** [TODO: insert row count from scan output]
- **Key Variables:** [TODO: list selected columns from PUMS dictionary, e.g., AGEP, SEX, PWGTP]

### Scan Results
```
[TODO: Insert full output from: dataforensics scan data/raw/acs_pums_extract.csv --rules schemas/acs_pums_rules.yaml]
```

**Summary:**
- **Total Issues Found:** [TODO: count of errors, warnings, suggestions]
- **Data Completeness:** [TODO: % non-null rows and key statistics]
- **Most Interesting Finding:** [TODO: describe one notable data quality issue caught by scan, e.g., unexpected value distributions, weight column anomalies]

---

## Dataset 3: OpenNeuro Participants File

### Raw Data Summary
- **Source:** OpenNeuro (https://openneuro.org/)
- **Dataset ID:** [TODO: fill in once selected, e.g., ds999999]
- **License:** [TODO: verify and record license from OpenNeuro page, e.g., CC0, CC-BY-4.0]
- **File:** `data/raw/openneuro_participants.tsv`
- **Rows (approx.):** [TODO: insert row count from scan output]

### Scan Results
```
[TODO: Insert full output from: dataforensics scan data/raw/openneuro_participants.tsv]
```

**Summary:**
- **Total Issues Found:** [TODO: count of errors, warnings, suggestions]
- **Data Completeness:** [TODO: % non-null rows and key statistics]
- **Most Interesting Finding:** [TODO: describe one notable finding, e.g., PII pattern detection, missing required columns, age distribution anomalies]

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

### Output Structure
[TODO: After running the command, describe the output files generated in `data/cleaned/wonder_pums_harmonized/`]

### Key Outputs
- **CDC WONDER harmonized:** `data/cleaned/wonder_pums_harmonized/cdc_wonder_export.harmonized.csv`
  - **Rows:** [TODO: insert actual row count]
  - **Columns after mapping:** [TODO: list harmonized column names]

- **ACS PUMS harmonized:** `data/cleaned/wonder_pums_harmonized/acs_pums_extract.harmonized.csv`
  - **Rows:** [TODO: insert actual row count]
  - **Columns after mapping:** [TODO: list harmonized column names and note any value remappings applied]

### Why Two Separate Tables, Not One Merged Table?
[TODO: After observing the output, explain the design decision. Expected reasoning:
- CDC WONDER is aggregate (county-level summary)
- ACS PUMS is microdata (person-level records)
- These are fundamentally different granularities and cannot be row-merged
- The crosswalk enables column-name alignment for comparison/analysis workflows, not database joins
Fill in with specific examples from your actual output.]

### Interesting Harmonization Findings
[TODO: Describe any column mismatches, value mapping surprises, or data-quality issues that emerged only when attempting to harmonize across sources]

---

## Summary & Next Steps

[TODO: Once all three datasets have been scanned and harmonized, write a 3-4 sentence summary noting:
- What the most common data issues were across the three datasets
- How the harmonization workflow helped surface source-specific vs. source-agnostic problems
- What manual follow-up or schema refinement the real data revealed]

**Outstanding Work:**
- Real data files remain in `data/raw/` (gitignored).
- Schema YAML files are ready for refinement against actual exports.
- Commands in this document can be re-run if rules or crosswalk are adjusted.

---

Generated as Task 17 scaffold (2026-08-24).
