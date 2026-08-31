# Example datasets

Three real, unmodified extracts from public U.S. government microdata releases, each subsampled from a much larger official file down to a demo-appropriate size. Every value is a genuine survey/exam response — nothing here is synthetic. All three sources are U.S. federal government works and are in the public domain (no license restrictions, no attribution legally required).

Selected specifically because each carries a well-documented, real messiness pattern DataForensics is built to catch — not curated to look artificially clean.

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

## `nhanes_health_exam.csv`

**Source:** CDC/NCHS National Health and Nutrition Examination Survey (NHANES), August 2021–August 2023 cycle. Merged from the Demographics, Body Measures, and Smoking Questionnaire components on the shared `SEQN` respondent ID.
**Downloaded from:** https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2021/DataFiles/DEMO_L.xpt, `BMX_L.xpt`, `SMQ_L.xpt`
**Documentation:** https://wwwn.cdc.gov/nchs/nhanes/continuousnhanes/default.aspx?Cycle=2021-2023

466 participant records, 13 columns, subsampled from 11,933 demographics records (stratified to guarantee the messy examples below survive subsampling, then shuffled).

| Column | Original NHANES variable | Notes |
|---|---|---|
| `respondent_id` | `SEQN` | |
| `sex` | `RIAGENDR` | |
| `age_years` | `RIDAGEYR` | |
| `race_ethnicity_code` | `RIDRETH3` | |
| `education_level` | `DMDEDUC2` | Only asked of adults 20+ — missing for children, a real skip-pattern |
| `marital_status` | `DMDMARTZ` | |
| `income_poverty_ratio` | `INDFMPIR` | **Genuinely top-coded at 5.00** ("5 or more") |
| `weight_kg` | `BMXWT` | |
| `height_cm` | `BMXHT` | |
| `bmi` | `BMXBMI` | |
| `waist_cm` | `BMXWAIST` | |
| `smoked_100_cigarettes` | `SMQ020` | 1/2 = yes/no, 7/9 = don't know/refused |
| `currently_smokes` | `SMQ040` | Missing for most respondents (only asked if the above was "yes") — a real skip-pattern |

## Regenerating these files

Not automated (deliberately — these are static, curated demo files, not a build step). To refresh with a newer survey cycle: download the source files linked above, load with `pandas.read_sas(..., format="xport")` for the CDC files or `pandas.read_csv` for PUMS, select/rename the columns in the tables above, and subsample. Decode any `bytes`-typed columns (SAS string columns come back as raw bytes from `pandas.read_sas`) before writing to CSV.
