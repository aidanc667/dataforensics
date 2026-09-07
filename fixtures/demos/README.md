# Example datasets

Two real, unmodified extracts from public U.S. government microdata releases, each subsampled from a much larger official file down to a demo-appropriate size. Every value is a genuine survey response — nothing here is synthetic. Both sources are U.S. federal government works and are in the public domain (no license restrictions, no attribution legally required).

Selected specifically because each carries a well-documented, real messiness pattern DataForensics is built to catch — not curated to look artificially clean.

The third example, `messy_csv_example.csv`, is different on purpose: it's synthetic. Real government microdata releases are already pre-coded (numeric category codes, not free-text labels) and contain no date columns at all, so genuinely they can never exercise a large share of what this tool does — category-spelling standardization, ambiguous-date detection, cross-column date ordering, near-duplicate-entity detection, unit-mixing — or the Review & Approve workflow those checks feed into (nothing in ACS PUMS or BRFSS ever needs an approved change). `messy_csv_example.csv` fills that gap.

## `acs_pums_person_dc.csv`

**Source:** U.S. Census Bureau, 2023 American Community Survey (ACS) 1-Year Public Use Microdata Sample (PUMS), person records for the District of Columbia.
**Downloaded from:** https://www2.census.gov/programs-surveys/acs/data/pums/2023/1-Year/csv_pdc.zip
**Documentation:** https://www.census.gov/programs-surveys/acs/microdata.html

499 person records, 12 columns, subsampled from the full 6,735-row DC person file (stratified to guarantee the messy examples below survive subsampling, then shuffled).

| Column | Original PUMS variable | Notes |
|---|---|---|
| `household_id` | `SERIALNO` | Shared key — pair with the household file for a one-to-many multi-file demo |
| `person_number` | `SPORDER` | |
| `puma_area_code` | `PUMA` | |
| `age_years` | `AGEP` | |
| `sex` | `SEX` | 1 = male, 2 = female |
| `race_code` | `RAC1P` | Census race code, 1–9 |
| `education_code` | `SCHL` | Missing for children under 3 — a real skip-pattern |
| `marital_status` | `MAR` | |
| `wages_income` | `WAGP` | |
| `total_personal_income` | `PINCP` | |
| `income_to_poverty_ratio` | `POVPIP` | **Genuinely top-coded at 501** ("500% of poverty or greater") per Census convention |
| `employment_status_code` | `ESR` | |

## `brfss_survey_sample.csv`

**Source:** CDC Behavioral Risk Factor Surveillance System (BRFSS), 2023 combined landline/cellphone survey.
**Downloaded from:** https://www.cdc.gov/brfss/annual_data/2023/files/LLCP2023XPT.zip
**Documentation:** https://www.cdc.gov/brfss/annual_data/annual_2023.html

580 respondent records, 14 columns, subsampled from a 300,000-row slice of the full 433,323-row national file (stratified to guarantee the messy examples below survive subsampling, then shuffled).

| Column | Original BRFSS variable | Notes |
|---|---|---|
| `respondent_id` | `SEQNO` | |
| `state_fips` | `_STATE` | |
| `sex` | `SEXVAR` | |
| `age_years` | `_AGE80` | **Top-coded at 80** ("80 or older") |
| `marital_status` | `MARITAL` | |
| `education_level` | `EDUCA` | |
| `employment_status` | `EMPLOY1` | |
| `income_bracket` | `INCOME3` | |
| `general_health` | `GENHLTH` | |
| `weight_lbs` | `WEIGHT2` | Contains literal `9999`/`7777` sentinel codes (refused/don't know) |
| `height_ft_in` | `HEIGHT3` | Contains literal `9999`/`7777` sentinel codes |
| `smoked_100_cigarettes` | `SMOKE100` | 1/2 = yes/no, 7/9 = don't know/refused |
| `currently_smokes` | `SMOKDAY2` | Missing for anyone who answered "no" above — a real skip-pattern |
| `diabetes_status` | `DIABETE4` | |

## `messy_csv_example.csv`

**Source:** Synthetic. Not real data — no provenance, no real people. 20 fabricated clinical-intake records, 13 columns, hand-constructed so every planted issue is verified to actually fire against DataForensics' real detection code (not just eyeballed) before being committed.

| Issue planted | Where | What it demonstrates |
|---|---|---|
| Inconsistent category spelling | `sex`: `Female`/`FEMALE`/`female`, `Male`/`male`/`" Male "` | Fuzzy category-cluster merge suggestion (needs approval) |
| Literal missing-value codes | `smoking_status`: `-99`, `Unknown` | Candidate sentinel mapping (needs approval) |
| Ambiguous date format | `admission_date`: one `03/04/2024` amid ISO dates | Ambiguous-date format picker (needs approval) |
| Impossible date ordering (naming-convention pair) | `admission_date`/`discharge_date` reversed for one patient | Cross-column ordering violation |
| Impossible date ordering (semantic role) | `birth_date` after `admission_date` for one patient | Birth-date-after-other-date violation |
| Near-duplicate entity | Same name + birth date, two different `participant_id`s | Duplicate-entity detection |
| Mixed measurement units | `weight_lbs`: three values actually recorded in kg | Unit-mixing detection (ratio matches the real kg↔lb conversion factor) |
| Conditional column inconsistency | `has_spouse` = No but `spouse_name` filled in | Conditional flag/detail column violation |
| PII pasted into free text | A phone number and an email address inside `notes` | Content-based PII detection |
| Whitespace anomalies | Leading/trailing/doubled spaces in `sex` and `spouse_name` | Whitespace-anomaly detection |

Unlike ACS PUMS and BRFSS, this file genuinely needs approved changes — loading it and clicking through Review & Approve exercises the part of the workflow the two real datasets never touch.

## Regenerating these files

The two real datasets are not automated (deliberately — these are static, curated demo files, not a build step). To refresh with a newer survey cycle: download the source files linked above, load with `pandas.read_sas(..., format="xport")` for the CDC files or `pandas.read_csv` for PUMS, select/rename the columns in the tables above, and subsample. Decode any `bytes`-typed columns (SAS string columns come back as raw bytes from `pandas.read_sas`) before writing to CSV.

`messy_csv_example.csv` is hand-edited directly — if you add a new planted issue, verify it actually fires against the real `investigate.py`/`dictionary.py` functions before committing, the same way every issue currently in the file was verified.
