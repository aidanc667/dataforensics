# DataForensics

[![CI](https://github.com/aidanc667/dataforensics/actions/workflows/ci.yml/badge.svg)](https://github.com/aidanc667/dataforensics/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

Data cleaning with an audit trail.

Upload a CSV, Excel or JSON file. DataForensics runs 51 checks, shows you the rows behind every problem it finds, changes only what you approve, and logs every changed cell.

**[Try the live app](https://dataforensics.streamlit.app/)**

## How it works

**Upload → Learn issues & insights → Review & fix → Cleaned dataset**

1. **Upload** a file, or load one of the three sample datasets.
2. **Overview and Issues** list what the checks found, most serious first, with the actual rows as evidence. Nothing has changed yet.
3. **Review & fix** lists every fix the checks can offer. *Approve all recommended fixes* ticks the safe ones in one click; anything that needs judgment is left for you.
4. **Results** re-runs every check on the cleaned file, shows the score before and after, and lists every change. Download the cleaned CSV, the audit trail and an HTML report.

## What it checks

51 checks, grouped as:

- **Missing data:** codes stored as values (`9999`, `-99`, `Refused`), columns that go missing together, and missing rates that differ by group.
- **Duplicates:** exact duplicate rows, and the same person under two IDs.
- **Consistency:** spelling variants (`Never` / `never`), synonym codes (`M` / `Male`), rare categories, unlikely combinations.
- **Dates:** ambiguous formats (03/04/2024), future dates, gaps in coverage, mixed time zones.
- **Cross-column:** discharge before admission, birth date after a visit, `has_spouse = No` with a spouse's name filled in, age that doesn't match date of birth.
- **Format and spreadsheet damage:** stray spaces, hidden characters, broken encoding (`JosÃ©`), `$1,200` stored as text, ZIP codes that lost their leading zero, dates stored as Excel numbers, IDs turned into scientific notation.
- **Values:** outliers, mixed kg/lb units, top-coding, round-number heaping.
- **Privacy:** emails, phone numbers and SSNs in free-text columns.

The **Checks** page in the app describes each one.

## What it changes

**Safe fixes** (included in *Approve all*): trim whitespace, remove hidden characters, repair encoding, turn `$1,200` into `1200`, restore leading zeros, convert Excel dates, merge spelling variants and synonym codes, map missing-value codes to blank or `Missing`, convert clearly separated kg/lb mixes, and remove exact duplicate rows.

**Judgment calls** (always ticked one by one): cap or empty outliers, mask personal data in text, and fill gaps with the median or most common value. Fills aren't offered where a blank means "not applicable", such as income for children.

Every change is logged with its row, column, old value, new value and rule. Before the cleaned file is offered, it's checked against the original: same columns, no lost records, and no records merged under the same ID.

## Insights

The Insights tab ranks the strongest patterns in the data in plain language, for example: "puma_area_code = 104 are 3.2× as likely to have race_code = 2". Each pattern is re-tested within age and sex groups. Patterns that are mostly explained by another column, or are true by definition (wages vs. total income), are held back and listed with the reason. You can upload a codebook (`column,value,label`) so codes read as `3 (Widowed)`.

## Sample datasets

- **BRFSS** (CDC): real health-survey answers with `7777`/`9999` missing-value codes and age capped at 80.
- **ACS PUMS** (U.S. Census): real person records with income top-coding and skip-pattern gaps.
- **Messy CSV Example**: a small synthetic intake form built to trigger every fix.

Sources and details are in [`fixtures/demos/README.md`](fixtures/demos/README.md).

More detail: [`ARCHITECTURE.md`](ARCHITECTURE.md) (design and safety rules) · [`WRITEUP.md`](WRITEUP.md) (benchmarks and findings).
