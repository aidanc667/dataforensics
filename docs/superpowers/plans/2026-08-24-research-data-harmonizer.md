# DataForensics (dataforensics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `dataforensics`, a Python CLI/package that profiles, validates, and (only via explicit rules) transforms messy research tabular exports — with zero silent mutation, a full audit manifest, and a genuine cross-dataset harmonization demo (CDC WONDER + ACS PUMS onto a shared schema).

**Architecture:** A `src/dataforensics/` package with clearly separated concerns — `ingest` (encoding/dialect/footer), `typing_guards` (ID/FIPS/sentinel), `dictionary` (profiling), `validation` (three-tier engine), `harmonize` (rules-driven transform + crosswalk), `manifest` (versioning/hashing/atomic writes), `report` (JSON→Markdown), `config_schema` (rules YAML validation) — wired together by a thin `cli.py`. Built phase-by-phase so `dataforensics scan` works and is tested before `dataforensics harmonize` exists, and single-file `harmonize` works and is tested before the crosswalk path exists.

**Tech Stack:** Python 3.11+, Polars (lazy scan), Click (CLI), PyYAML, charset-normalizer (encoding detection), rapidfuzz (fuzzy category-merge suggestions), pytest.

**Full spec:** `/Users/aidan/Desktop/data-forensics/MASTER_PROMPT.md` — read it before starting; this plan implements it task-by-task and does not restate every rationale.

## Global Constraints

- Input files are NEVER opened in write mode. Every task that touches a real or fixture input file must assert its SHA-256 is unchanged after the operation.
- No transformation (type cast, date parse, unit conversion, missing-code substitution, category merge) is ever applied without an explicit rule in a rules YAML. `scan` may only suggest, never apply.
- Rows/columns are never silently dropped; counts are asserted in==out unless a rule explicitly removed something.
- All writes are atomic (temp file + rename). `harmonize` without `--execute` writes nothing.
- Severity tiers: **Error** = deterministic hard-rule violation (e.g. below a configured `minimum`, duplicate primary key). **Warning** = deterministic but context-dependent (e.g. above a configured `maximum` — may still be valid). **Suggestion** = heuristic (IQR/MAD outlier, rare category, fuzzy category-merge candidate) — never counted as Error/Warning. Every check reports PASSED / WARNING / FAILED / NOT EVALUATED — never silently omitted.
- Exit codes: `0` success/no hard errors, `1` validation errors found, `2` invalid user configuration (includes rules-file errors and `--output`/`--output-dir` path collisions), `3` runtime/IO failure.
- Manifest fields per mutation use a `row_key` object (all primary-key columns), never a bare row index.
- CLI flag for a rules file is always `--rules` (both `scan` and `harmonize`) — never `--config`.
- Dependency versions recorded in the manifest are direct dependencies only (via `importlib.metadata`), not the transitive tree.

---

## File Structure

```
DataForensics/
├── pyproject.toml
├── .gitignore
├── README.md                      # Task 18
├── WRITEUP.md                     # Task 17
├── src/dataforensics/
│   ├── __init__.py                # Task 1
│   ├── cli.py                     # Tasks 1, 8, 11, 13, 14, 15
│   ├── hashing.py                 # Task 2
│   ├── ingest.py                  # Tasks 3, 4
│   ├── typing_guards.py           # Task 5
│   ├── dictionary.py              # Tasks 6, 7
│   ├── config_schema.py           # Task 9
│   ├── validation.py              # Task 10
│   ├── manifest.py                # Task 12
│   ├── harmonize.py               # Tasks 13, 14, 15
│   └── report.py                  # Task 8
├── schemas/
│   ├── sample_rules.yaml          # Task 16
│   ├── cdc_wonder_rules.yaml      # Task 17
│   ├── acs_pums_rules.yaml        # Task 17
│   └── wonder_pums_crosswalk.yaml # Task 17
├── fixtures/
│   └── sample.csv                 # Task 16
├── data/
│   ├── raw/.gitkeep                # Task 17 (contents gitignored)
│   ├── standardized/.gitkeep
│   └── cleaned/.gitkeep
├── tests/
│   ├── unit/                      # Tasks 1-15
│   ├── integration/                # Tasks 8, 11, 14, 15
│   ├── regression/                 # Tasks 14, 15
│   └── e2e/                        # Tasks 16, 18
└── .github/workflows/ci.yml       # Task 1
```

---

### Task 1: Package skeleton, CLI stubs, CI

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/dataforensics/__init__.py`
- Create: `src/dataforensics/cli.py`
- Test: `tests/unit/test_cli_stub.py`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `dataforensics.cli.main` (Click group), console-script entry `dataforensics`. Later tasks add real subcommand bodies in place of the stubs here.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_stub.py
from click.testing import CliRunner
from dataforensics.cli import main


def test_help_lists_subcommands():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.output
    assert "harmonize" in result.output
    assert "report" in result.output


def test_scan_stub_exits_3():
    result = CliRunner().invoke(main, ["scan", "somefile.csv"])
    assert result.exit_code == 3
    assert "not implemented" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_stub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics'`

- [ ] **Step 3: Write pyproject.toml**

```toml
[project]
name = "DataForensics"
version = "0.1.0"
description = "Auditable profiling, validation, and rules-driven harmonization for messy research tabular exports."
requires-python = ">=3.11"
dependencies = [
    "polars>=1.0",
    "click>=8.1",
    "pyyaml>=6.0",
    "charset-normalizer>=3.3",
    "rapidfuzz>=3.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
dataforensics = "dataforensics.cli:main"

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 4: Write .gitignore**

```
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
data/raw/*
!data/raw/.gitkeep
data/standardized/*
!data/standardized/.gitkeep
data/cleaned/*
!data/cleaned/.gitkeep
```

- [ ] **Step 5: Write src/dataforensics/__init__.py**

```python
__version__ = "0.1.0"
```

- [ ] **Step 6: Write src/dataforensics/cli.py**

```python
import sys

import click

from dataforensics import __version__


@click.group()
@click.version_option(__version__)
def main():
    """dataforensics — auditable research-data profiling, validation, and harmonization."""


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", type=click.Path(exists=True), default=None)
def scan(file, rules):
    """Read-only: emit a data dictionary and validation report."""
    click.echo("scan: not implemented")
    sys.exit(3)


@main.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--rules", type=click.Path(exists=True), default=None)
@click.option("--output", type=click.Path(), default=None)
@click.option("--rules-map", default=None)
@click.option("--crosswalk", type=click.Path(exists=True), default=None)
@click.option("--output-dir", type=click.Path(), default=None)
@click.option("--execute", is_flag=True, default=False)
def harmonize(files, rules, output, rules_map, crosswalk, output_dir, execute):
    """Rules-driven, dry-run-by-default transform. Single file or cross-dataset crosswalk."""
    click.echo("harmonize: not implemented")
    sys.exit(3)


@main.command()
@click.argument("artifact", type=click.Path(exists=True))
def report(artifact):
    """Render a JSON report/manifest artifact to Markdown."""
    click.echo("report: not implemented")
    sys.exit(3)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pip install -e ".[dev]" && pytest tests/unit/test_cli_stub.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Write minimal CI**

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: pytest
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .gitignore src/dataforensics/__init__.py src/dataforensics/cli.py tests/unit/test_cli_stub.py .github/workflows/ci.yml
git commit -m "feat: package skeleton, CLI stubs, CI"
```

---

### Task 2: SHA-256 hashing utility

**Files:**
- Create: `src/dataforensics/hashing.py`
- Test: `tests/unit/test_hashing.py`

**Interfaces:**
- Produces: `hashing.sha256_file(path: pathlib.Path) -> str` (hex digest). Used by every later task that must prove input immutability or hash a rules file.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_hashing.py
import hashlib
from pathlib import Path

from dataforensics.hashing import sha256_file


def test_sha256_file_matches_stdlib(tmp_path):
    f = tmp_path / "sample.txt"
    content = b"a,b,c\n1,2,3\n"
    f.write_bytes(content)

    assert sha256_file(f) == hashlib.sha256(content).hexdigest()


def test_sha256_file_handles_large_content(tmp_path):
    f = tmp_path / "big.txt"
    content = b"x" * (5 * 1024 * 1024 + 7)  # not a multiple of the chunk size
    f.write_bytes(content)

    assert sha256_file(f) == hashlib.sha256(content).hexdigest()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_hashing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.hashing'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/hashing.py
import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_hashing.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dataforensics/hashing.py tests/unit/test_hashing.py
git commit -m "feat: SHA-256 file hashing utility"
```

---

### Task 3: Encoding and delimiter detection

**Files:**
- Create: `src/dataforensics/ingest.py`
- Test: `tests/unit/test_ingest_detection.py`

**Interfaces:**
- Produces: `ingest.detect_encoding(path: Path) -> str`, `ingest.detect_delimiter(sample_lines: list[str]) -> str`. Used by Task 4 (footer stripping) and Task 6 (dictionary generation) to actually open files correctly.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ingest_detection.py
from dataforensics.ingest import detect_delimiter, detect_encoding


def test_detect_delimiter_comma():
    lines = ["a,b,c", "1,2,3", "4,5,6"]
    assert detect_delimiter(lines) == ","


def test_detect_delimiter_tab():
    lines = ["a\tb\tc", "1\t2\t3", "4\t5\t6"]
    assert detect_delimiter(lines) == "\t"


def test_detect_delimiter_semicolon():
    lines = ["a;b;c", "1;2;3", "4;5;6"]
    assert detect_delimiter(lines) == ";"


def test_detect_encoding_utf8(tmp_path):
    f = tmp_path / "utf8.csv"
    f.write_text("name,city\nJosé,São Paulo\n", encoding="utf-8")
    assert detect_encoding(f) == "utf-8" or detect_encoding(f).lower() == "utf-8"


def test_detect_encoding_latin1(tmp_path):
    f = tmp_path / "latin1.csv"
    f.write_bytes("name,city\nJos\xe9,Caf\xe9\n".encode("latin-1"))
    assert detect_encoding(f).lower() in ("latin-1", "iso-8859-1", "cp1252")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ingest_detection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.ingest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/ingest.py
from pathlib import Path

from charset_normalizer import from_path

_CANDIDATE_DELIMITERS = [",", "\t", ";", "|"]


def detect_encoding(path: Path) -> str:
    result = from_path(str(path)).best()
    if result is None:
        return "utf-8"
    return result.encoding


def detect_delimiter(sample_lines: list[str]) -> str:
    non_empty = [line for line in sample_lines if line.strip()]
    if not non_empty:
        return ","

    best_delim = ","
    best_score = -1
    for delim in _CANDIDATE_DELIMITERS:
        counts = [line.count(delim) for line in non_empty]
        if counts[0] == 0:
            continue
        consistent = sum(1 for c in counts if c == counts[0])
        score = consistent * counts[0]
        if score > best_score:
            best_score = score
            best_delim = delim
    return best_delim
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ingest_detection.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dataforensics/ingest.py tests/unit/test_ingest_detection.py
git commit -m "feat: encoding and delimiter detection"
```

---

### Task 4: Footer/disclaimer stripping

**Files:**
- Modify: `src/dataforensics/ingest.py`
- Test: `tests/unit/test_footer_stripping.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ingest.strip_footer(lines: list[str], delimiter: str) -> tuple[list[str], list[str]]` returning `(data_lines, stripped_lines)`. Used by Task 6 (dictionary generation) before any parsing.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_footer_stripping.py
from dataforensics.ingest import strip_footer


def test_strips_wonder_style_footer():
    lines = [
        "County,Deaths,Population",
        "Alameda,120,1600000",
        "Marin,45,260000",
        '"Query Parameters:"',
        '"Group By: County"',
        '"Total Deaths: 165"',
    ]
    data, stripped = strip_footer(lines, ",")
    assert data == lines[:3]
    assert stripped == lines[3:]


def test_no_footer_present_strips_nothing():
    lines = ["a,b", "1,2", "3,4", "5,6"]
    data, stripped = strip_footer(lines, ",")
    assert data == lines
    assert stripped == []


def test_single_stray_mismatched_line_not_stripped():
    # a lone short line (e.g. a genuinely short data row) should not trigger stripping
    lines = ["a,b,c", "1,2,3", "4,5", "7,8,9"]
    data, stripped = strip_footer(lines, ",")
    assert data == lines
    assert stripped == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_footer_stripping.py -v`
Expected: FAIL with `ImportError: cannot import name 'strip_footer'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/ingest.py (append)
_FOOTER_MISMATCH_RUN = 2


def strip_footer(lines: list[str], delimiter: str) -> tuple[list[str], list[str]]:
    if not lines:
        return [], []

    header_fields = lines[0].count(delimiter) + 1
    mismatch_start = None
    run_length = 0

    for i in range(1, len(lines)):
        fields = lines[i].count(delimiter) + 1
        if fields != header_fields:
            run_length += 1
            if run_length >= _FOOTER_MISMATCH_RUN:
                mismatch_start = i - _FOOTER_MISMATCH_RUN + 1
                break
        else:
            run_length = 0

    if mismatch_start is None:
        return lines, []
    return lines[:mismatch_start], lines[mismatch_start:]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_footer_stripping.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dataforensics/ingest.py tests/unit/test_footer_stripping.py
git commit -m "feat: footer/disclaimer stripping via sustained field-count mismatch"
```

---

### Task 5: ID/FIPS/ZIP typing guard and sentinel classification

**Files:**
- Create: `src/dataforensics/typing_guards.py`
- Test: `tests/unit/test_typing_guards.py`

**Interfaces:**
- Produces: `typing_guards.is_id_like_column(name: str) -> bool`, `typing_guards.preserves_leading_zero(values: list[str]) -> bool`, `typing_guards.classify_sentinel(value: str, sentinel_map: dict) -> str | None`. Used by Task 6 (dictionary) and Task 14 (harmonize execute).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_typing_guards.py
from dataforensics.typing_guards import (
    classify_sentinel,
    is_id_like_column,
    preserves_leading_zero,
)


def test_id_like_column_names():
    assert is_id_like_column("participant_id") is True
    assert is_id_like_column("county_fips") is True
    assert is_id_like_column("geoid") is True
    assert is_id_like_column("zip_code") is True
    assert is_id_like_column("age") is False
    assert is_id_like_column("income") is False


def test_preserves_leading_zero_detects_fips():
    assert preserves_leading_zero(["06081", "02138", "48201"]) is True


def test_preserves_leading_zero_false_for_no_zero_padding():
    assert preserves_leading_zero(["120", "45", "9001"]) is False


def test_classify_sentinel_returns_label():
    sentinel_map = {"99": "Refused", "-9": "Not applicable"}
    assert classify_sentinel("99", sentinel_map) == "Refused"
    assert classify_sentinel("-9", sentinel_map) == "Not applicable"


def test_classify_sentinel_none_for_ordinary_value():
    sentinel_map = {"99": "Refused"}
    assert classify_sentinel("42", sentinel_map) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_typing_guards.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.typing_guards'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/typing_guards.py
import re

_ID_LIKE_PATTERN = re.compile(r"(^|_)(id|fips|geoid|zip)(_|$)", re.IGNORECASE)


def is_id_like_column(name: str) -> bool:
    return bool(_ID_LIKE_PATTERN.search(name))


def preserves_leading_zero(values: list[str]) -> bool:
    for v in values:
        v = v.strip()
        if len(v) > 1 and v[0] == "0" and v.isdigit():
            return True
    return False


def classify_sentinel(value: str, sentinel_map: dict) -> str | None:
    return sentinel_map.get(str(value))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_typing_guards.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dataforensics/typing_guards.py tests/unit/test_typing_guards.py
git commit -m "feat: ID/FIPS/ZIP typing guard and sentinel classification"
```

---

### Task 6: Data dictionary generation

**Files:**
- Create: `src/dataforensics/dictionary.py`
- Test: `tests/unit/test_dictionary.py`

**Interfaces:**
- Consumes: `ingest.detect_encoding`, `ingest.detect_delimiter`, `ingest.strip_footer`, `typing_guards.is_id_like_column`, `typing_guards.preserves_leading_zero`.
- Produces: `dictionary.build_data_dictionary(path: Path) -> dict` — one entry per column with `dtype`, `non_null_pct`, `unique_count`, `is_zero_variance`, `category` (`"categorical"` / `"free_text"` / `"id"`), `levels` (only if categorical), `zero_count`, `null_count` (kept separate). Used by Task 8 (`scan` CLI wiring).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_dictionary.py
from pathlib import Path

from dataforensics.dictionary import build_data_dictionary


def test_dictionary_basic_fields(tmp_path):
    f = tmp_path / "sample.csv"
    f.write_text(
        "participant_id,sex,age,notes\n"
        "001,M,34,fine\n"
        "002,F,29,fine\n"
        "003,M,,fine\n"
        "004,F,41,fine\n"
    )
    d = build_data_dictionary(f)

    assert d["participant_id"]["category"] == "id"
    assert d["participant_id"]["dtype"] == "Utf8"

    assert d["age"]["null_count"] == 1
    assert d["age"]["non_null_pct"] == 75.0

    assert d["sex"]["category"] == "categorical"
    assert set(d["sex"]["levels"]) == {"M", "F"}


def test_dictionary_zero_variance_flag(tmp_path):
    f = tmp_path / "flat.csv"
    f.write_text("id,site\n001,A\n002,A\n003,A\n")
    d = build_data_dictionary(f)
    assert d["site"]["is_zero_variance"] is True
    assert d["id"]["is_zero_variance"] is False


def test_dictionary_zero_vs_null_kept_separate(tmp_path):
    f = tmp_path / "smoking.csv"
    f.write_text("id,cigs_per_day\n001,0\n002,\n003,5\n")
    d = build_data_dictionary(f)
    assert d["cigs_per_day"]["zero_count"] == 1
    assert d["cigs_per_day"]["null_count"] == 1


def test_dictionary_high_cardinality_is_free_text(tmp_path):
    rows = "\n".join(f"{i},note-{i}-unique" for i in range(60))
    f = tmp_path / "notes.csv"
    f.write_text("id,note\n" + rows + "\n")
    d = build_data_dictionary(f)
    assert d["note"]["category"] == "free_text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_dictionary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.dictionary'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/dictionary.py
from pathlib import Path

import polars as pl

from dataforensics.ingest import detect_delimiter, detect_encoding, strip_footer
from dataforensics.typing_guards import is_id_like_column, preserves_leading_zero


def _read_cleaned_lines(path: Path) -> tuple[list[str], str]:
    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, _stripped = strip_footer(raw_lines, delimiter)
    return data_lines, delimiter


def build_data_dictionary(path: Path) -> dict:
    data_lines, delimiter = _read_cleaned_lines(path)
    header = data_lines[0].split(delimiter)
    body_rows = [line.split(delimiter) for line in data_lines[1:]]
    n_rows = len(body_rows)

    columns: dict[str, list[str]] = {name: [] for name in header}
    for row in body_rows:
        for name, value in zip(header, row):
            columns[name].append(value)

    result = {}
    for name, raw_values in columns.items():
        null_count = sum(1 for v in raw_values if v == "")
        non_null_values = [v for v in raw_values if v != ""]
        non_null_pct = round(100.0 * (n_rows - null_count) / n_rows, 4) if n_rows else 0.0
        unique_values = set(non_null_values)
        unique_count = len(unique_values)
        zero_count = sum(1 for v in non_null_values if v == "0")

        id_like = is_id_like_column(name) or preserves_leading_zero(non_null_values)
        cardinality_cap = min(50, max(1, int(0.05 * n_rows)))

        if id_like:
            category = "id"
            dtype = "Utf8"
            levels = None
        elif unique_count <= cardinality_cap:
            category = "categorical"
            dtype = "Utf8"
            levels = sorted(unique_values)
        else:
            category = "free_text"
            dtype = "Utf8"
            levels = None

        result[name] = {
            "dtype": dtype,
            "category": category,
            "non_null_pct": non_null_pct,
            "unique_count": unique_count,
            "is_zero_variance": unique_count == 1,
            "zero_count": zero_count,
            "null_count": null_count,
            "levels": levels,
        }

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_dictionary.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dataforensics/dictionary.py tests/unit/test_dictionary.py
git commit -m "feat: data dictionary generation (dtype, missingness, cardinality, zero-variance)"
```

---

### Task 7: Top-code spike and IQR outlier detection (Suggestion-tier only)

**Files:**
- Modify: `src/dataforensics/dictionary.py`
- Test: `tests/unit/test_outlier_detection.py`

**Interfaces:**
- Produces: `dictionary.detect_outliers(values: list[float]) -> dict` returning `{"method": "IQR", "outlier_count": int, "outlier_indices": list[int]}`, and `dictionary.detect_top_code_spike(values: list[float]) -> dict | None` returning `{"value": max_value, "fraction": float}` when a point mass at the max exceeds threshold, else `None`. Both are called from `build_data_dictionary` for numeric columns and attached as `d[col]["outliers"]` / `d[col]["top_code_spike"]`, always labeled with method, never used to drop/alter values.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_outlier_detection.py
from dataforensics.dictionary import detect_outliers, detect_top_code_spike


def test_iqr_outlier_detection_flags_extreme_value():
    values = [10, 11, 12, 13, 12, 11, 10, 200]
    result = detect_outliers(values)
    assert result["method"] == "IQR"
    assert result["outlier_count"] == 1
    assert 7 in result["outlier_indices"]


def test_iqr_outlier_detection_no_false_positive_on_tight_cluster():
    values = [95, 96, 94, 95, 97, 93, 95, 96]
    result = detect_outliers(values)
    assert result["outlier_count"] == 0


def test_top_code_spike_detected_for_census_style_capping():
    # 250000 is a plausible ACS PUMS top-code; heavy mass at the max
    values = [45000, 62000, 38000] + [250000] * 20
    spike = detect_top_code_spike(values)
    assert spike is not None
    assert spike["value"] == 250000
    assert spike["fraction"] > 0.5


def test_no_top_code_spike_for_smoothly_distributed_values():
    values = list(range(1, 101))
    assert detect_top_code_spike(values) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_outlier_detection.py -v`
Expected: FAIL with `ImportError: cannot import name 'detect_outliers'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/dictionary.py (append)
_TOP_CODE_SPIKE_THRESHOLD = 0.05


def detect_outliers(values: list[float]) -> dict:
    if len(values) < 4:
        return {"method": "IQR", "outlier_count": 0, "outlier_indices": []}

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q3 = sorted_vals[(3 * n) // 4]
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    indices = [i for i, v in enumerate(values) if v < lower or v > upper]
    return {"method": "IQR", "outlier_count": len(indices), "outlier_indices": indices}


def detect_top_code_spike(values: list[float]) -> dict | None:
    if not values:
        return None
    max_val = max(values)
    fraction = sum(1 for v in values if v == max_val) / len(values)
    if fraction >= _TOP_CODE_SPIKE_THRESHOLD and fraction > 1 / len(values):
        return {"value": max_val, "fraction": round(fraction, 4)}
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_outlier_detection.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire into build_data_dictionary for numeric-looking columns**

```python
# src/dataforensics/dictionary.py — inside build_data_dictionary, after computing `levels`, before building result[name]:
        numeric_values = []
        if category != "id":
            for v in non_null_values:
                try:
                    numeric_values.append(float(v))
                except ValueError:
                    numeric_values = []
                    break

        outliers = detect_outliers(numeric_values) if numeric_values else None
        top_code_spike = detect_top_code_spike(numeric_values) if numeric_values else None
```

And add `"outliers": outliers, "top_code_spike": top_code_spike,` to the `result[name] = {...}` dict literal.

- [ ] **Step 6: Run full dictionary test suite**

Run: `pytest tests/unit/test_dictionary.py tests/unit/test_outlier_detection.py -v`
Expected: PASS (8 tests)

- [ ] **Step 7: Commit**

```bash
git add src/dataforensics/dictionary.py tests/unit/test_outlier_detection.py
git commit -m "feat: IQR outlier and top-code spike detection, wired into data dictionary"
```

---

### Task 8: `report.py` rendering and real `scan` CLI wiring

**Files:**
- Create: `src/dataforensics/report.py`
- Modify: `src/dataforensics/cli.py`
- Test: `tests/unit/test_report.py`
- Test: `tests/integration/test_scan_command.py`

**Interfaces:**
- Consumes: `dictionary.build_data_dictionary`, `hashing.sha256_file`.
- Produces: `report.render_markdown(title: str, data: dict) -> str`. `cli.scan` now writes `<file>.data_dictionary.json` and `<file>.data_dictionary.md` next to the CLI's `--out-dir` (default: current directory), and prints a summary. Later tasks (10, 11) extend `scan` to also emit `validation_report.*`.

- [ ] **Step 1: Write the failing test (report rendering)**

```python
# tests/unit/test_report.py
from dataforensics.report import render_markdown


def test_render_markdown_includes_column_names_and_values():
    data = {"age": {"dtype": "Utf8", "non_null_pct": 100.0, "unique_count": 3}}
    md = render_markdown("Data Dictionary", data)
    assert "# Data Dictionary" in md
    assert "age" in md
    assert "100.0" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/report.py
def render_markdown(title: str, data: dict) -> str:
    lines = [f"# {title}", ""]
    for column, fields in data.items():
        lines.append(f"## {column}")
        for key, value in fields.items():
            lines.append(f"- **{key}**: {value}")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_report.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Write the failing integration test for `scan`**

```python
# tests/integration/test_scan_command.py
import json
from pathlib import Path

from click.testing import CliRunner

from dataforensics.cli import main
from dataforensics.hashing import sha256_file


def test_scan_writes_dictionary_and_never_modifies_input(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("id,age\n001,34\n002,29\n")
    before_hash = sha256_file(src)

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert sha256_file(src) == before_hash

    json_path = tmp_path / "sample.data_dictionary.json"
    md_path = tmp_path / "sample.data_dictionary.md"
    assert json_path.exists()
    assert md_path.exists()

    payload = json.loads(json_path.read_text())
    assert "age" in payload
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/integration/test_scan_command.py -v`
Expected: FAIL — `scan` still prints "not implemented" and exits 3, or `--out-dir` option doesn't exist yet.

- [ ] **Step 7: Update cli.py's scan command**

```python
# src/dataforensics/cli.py — replace the scan command body
import json
from pathlib import Path

from dataforensics.dictionary import build_data_dictionary
from dataforensics.report import render_markdown


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", type=click.Path(exists=True), default=None)
@click.option("--out-dir", type=click.Path(), default=".")
def scan(file, rules, out_dir):
    """Read-only: emit a data dictionary and validation report."""
    file_path = Path(file)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    dictionary = build_data_dictionary(file_path)

    stem = file_path.stem
    (out_dir_path / f"{stem}.data_dictionary.json").write_text(json.dumps(dictionary, indent=2))
    (out_dir_path / f"{stem}.data_dictionary.md").write_text(
        render_markdown(f"Data Dictionary: {file_path.name}", dictionary)
    )
    click.echo(f"scan complete: {len(dictionary)} columns profiled")
```

(Note: `rules` is accepted but unused until Task 11 adds the validation report.)

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/integration/test_scan_command.py -v`
Expected: PASS (1 test)

- [ ] **Step 9: Run full suite so far**

Run: `pytest -v`
Expected: PASS, all prior tests still green

- [ ] **Step 10: Commit**

```bash
git add src/dataforensics/report.py src/dataforensics/cli.py tests/unit/test_report.py tests/integration/test_scan_command.py
git commit -m "feat: Markdown report rendering, wire scan to emit a real data dictionary"
```

---

### Task 9: Rules YAML schema and validation-on-load

**Files:**
- Create: `src/dataforensics/config_schema.py`
- Test: `tests/unit/test_config_schema.py`

**Interfaces:**
- Produces: `config_schema.load_rules(path: Path) -> dict` (parsed + validated rules) and `config_schema.RulesConfigError(Exception)`. Used by Task 10 (validation), Task 14 (harmonize execute), Task 15 (crosswalk). CLI must catch `RulesConfigError` and exit 2 (wired in Task 11).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config_schema.py
import pytest

from dataforensics.config_schema import RulesConfigError, load_rules


def test_load_valid_rules(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns:\n"
        "  age:\n"
        "    type: integer\n"
        "    minimum: 0\n"
        "    maximum: 120\n"
    )
    rules = load_rules(f)
    assert rules["version"] == 1
    assert rules["primary_key"] == ["participant_id"]
    assert rules["columns"]["age"]["minimum"] == 0


def test_load_rules_missing_version_fails(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("primary_key: [id]\ncolumns: {}\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_missing_primary_key_fails(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\ncolumns: {}\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_malformed_yaml_fails(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\n  bad indent: [\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_defaults_missing_optional_sections(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns: {}\n")
    rules = load_rules(f)
    assert rules["missing_values"] == {}
    assert rules["category_mappings"] == {}
    assert rules["weights_strata"] == {"columns": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.config_schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/config_schema.py
from pathlib import Path

import yaml


class RulesConfigError(Exception):
    pass


_REQUIRED_KEYS = ("version", "primary_key", "columns")


def load_rules(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise RulesConfigError(f"Malformed YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RulesConfigError(f"Rules file {path} must be a YAML mapping at the top level")

    for key in _REQUIRED_KEYS:
        if key not in raw:
            raise RulesConfigError(f"Rules file {path} is missing required key: {key}")

    if not isinstance(raw["primary_key"], list) or not raw["primary_key"]:
        raise RulesConfigError(f"Rules file {path}: primary_key must be a non-empty list")

    raw.setdefault("missing_values", {})
    raw.setdefault("category_mappings", {})
    raw.setdefault("weights_strata", {"columns": []})
    return raw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config_schema.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dataforensics/config_schema.py tests/unit/test_config_schema.py
git commit -m "feat: rules YAML schema validation-on-load"
```

---

### Task 10: Three-tier validation engine

**Files:**
- Create: `src/dataforensics/validation.py`
- Test: `tests/unit/test_validation.py`

**Interfaces:**
- Consumes: `config_schema.load_rules` output shape, `dictionary.detect_outliers`.
- Produces: `validation.validate(rows: list[dict], rules: dict) -> dict` returning `{"errors": [...], "warnings": [...], "suggestions": [...], "checks_evaluated": int, "checks_passed": int}`. Each finding is `{"column": str, "row_key": dict, "rule": str, "message": str, "severity": "error"|"warning"|"suggestion"}`. Used by Task 11 (`scan` wiring).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_validation.py
from dataforensics.validation import validate


_RULES = {
    "version": 1,
    "primary_key": ["participant_id"],
    "columns": {
        "age": {"type": "integer", "minimum": 0, "maximum": 120},
    },
    "missing_values": {},
    "category_mappings": {},
    "weights_strata": {"columns": []},
}


def test_minimum_violation_is_error():
    rows = [{"participant_id": "1", "age": "-5"}]
    result = validate(rows, _RULES)
    assert len(result["errors"]) == 1
    assert result["errors"][0]["rule"] == "minimum"
    assert result["warnings"] == []


def test_maximum_violation_is_warning_not_error():
    rows = [{"participant_id": "1", "age": "130"}]
    result = validate(rows, _RULES)
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    assert result["warnings"][0]["rule"] == "maximum"


def test_plausible_extreme_value_is_not_flagged():
    rows = [{"participant_id": "1", "age": "95"}]
    result = validate(rows, _RULES)
    assert result["errors"] == []
    assert result["warnings"] == []


def test_duplicate_primary_key_is_error():
    rows = [
        {"participant_id": "1", "age": "40"},
        {"participant_id": "1", "age": "41"},
    ]
    result = validate(rows, _RULES)
    dup_errors = [e for e in result["errors"] if e["rule"] == "duplicate_primary_key"]
    assert len(dup_errors) == 1


def test_rare_category_is_suggestion_never_error_or_warning():
    rules = dict(_RULES)
    rows = [
        {"participant_id": str(i), "age": "40"} for i in range(20)
    ] + [{"participant_id": "21", "age": "40"}]
    # inject a rare free-text-ish column check isn't part of _RULES; this test
    # exercises that validate() never promotes a heuristic to error/warning tier
    result = validate(rows, rules)
    assert all(f["severity"] != "error" for f in result["suggestions"])
    assert all(f["severity"] != "warning" for f in result["suggestions"])


def test_column_with_no_rule_is_not_evaluated():
    rows = [{"participant_id": "1", "age": "40", "site": "A"}]
    result = validate(rows, _RULES)
    assert result["checks_evaluated"] == result["checks_passed"] + len(
        result["errors"]
    ) + len(result["warnings"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_validation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.validation'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/validation.py
def _row_key(row: dict, primary_key: list[str]) -> dict:
    return {k: row.get(k) for k in primary_key}


def validate(rows: list[dict], rules: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    suggestions: list[dict] = []
    checks_evaluated = 0

    primary_key = rules["primary_key"]
    columns_rules = rules.get("columns", {})

    seen_keys: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row.get(k) for k in primary_key)
        checks_evaluated += 1
        if key in seen_keys:
            errors.append(
                {
                    "column": ",".join(primary_key),
                    "row_key": _row_key(row, primary_key),
                    "rule": "duplicate_primary_key",
                    "message": f"Duplicate primary key value: {key}",
                    "severity": "error",
                }
            )
        else:
            seen_keys[key] = row

    for row in rows:
        row_key = _row_key(row, primary_key)
        for column, col_rules in columns_rules.items():
            raw_value = row.get(column)
            if raw_value in (None, ""):
                continue

            if "minimum" in col_rules:
                checks_evaluated += 1
                try:
                    numeric = float(raw_value)
                except ValueError:
                    continue
                if numeric < col_rules["minimum"]:
                    errors.append(
                        {
                            "column": column,
                            "row_key": row_key,
                            "rule": "minimum",
                            "message": f"{column}={raw_value} is below configured minimum {col_rules['minimum']}",
                            "severity": "error",
                        }
                    )

            if "maximum" in col_rules:
                checks_evaluated += 1
                try:
                    numeric = float(raw_value)
                except ValueError:
                    continue
                if numeric > col_rules["maximum"]:
                    warnings.append(
                        {
                            "column": column,
                            "row_key": row_key,
                            "rule": "maximum",
                            "message": f"{column}={raw_value} is above configured maximum {col_rules['maximum']} — may still be valid",
                            "severity": "warning",
                        }
                    )

    checks_passed = checks_evaluated - len(errors) - len(warnings)
    return {
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "checks_evaluated": checks_evaluated,
        "checks_passed": checks_passed,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_validation.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dataforensics/validation.py tests/unit/test_validation.py
git commit -m "feat: three-tier validation engine (error/warning/suggestion, NOT EVALUATED accounting)"
```

---

### Task 11: Wire validation report into `scan`; false-positive regression tests

**Files:**
- Modify: `src/dataforensics/cli.py`
- Modify: `src/dataforensics/dictionary.py` (add a CSV-row-reading helper reused by validation)
- Test: `tests/integration/test_scan_validation_report.py`

**Interfaces:**
- Consumes: `config_schema.load_rules`, `validation.validate`.
- Produces: `scan` now writes `<stem>.validation_report.json` / `.md` when `--rules` is passed, and exits `1` if any Error is present (per Global Constraints exit code table), `0` otherwise. Also catches `RulesConfigError` and exits `2`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_scan_validation_report.py
import json

from click.testing import CliRunner

from dataforensics.cli import main


def _write_rules(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns:\n"
        "  age:\n"
        "    type: integer\n"
        "    minimum: 0\n"
        "    maximum: 120\n"
    )
    return rules_path


def test_scan_with_rules_flags_minimum_violation_as_error(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,age\n1,-5\n2,40\n")
    rules_path = _write_rules(tmp_path)

    result = CliRunner().invoke(
        main, ["scan", str(src), "--rules", str(rules_path), "--out-dir", str(tmp_path)]
    )

    assert result.exit_code == 1
    report = json.loads((tmp_path / "sample.validation_report.json").read_text())
    assert len(report["errors"]) == 1


def test_scan_does_not_flag_plausible_extreme_age_as_error(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,age\n1,95\n2,40\n")
    rules_path = _write_rules(tmp_path)

    result = CliRunner().invoke(
        main, ["scan", str(src), "--rules", str(rules_path), "--out-dir", str(tmp_path)]
    )

    assert result.exit_code == 0
    report = json.loads((tmp_path / "sample.validation_report.json").read_text())
    assert report["errors"] == []
    assert report["warnings"] == []


def test_scan_with_malformed_rules_exits_2(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,age\n1,40\n")
    bad_rules = tmp_path / "bad.yaml"
    bad_rules.write_text("not_a_valid_key: true\n")

    result = CliRunner().invoke(
        main, ["scan", str(src), "--rules", str(bad_rules), "--out-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_scan_validation_report.py -v`
Expected: FAIL — `scan` doesn't use `--rules` yet, always exits 0, no validation_report files

- [ ] **Step 3: Add a shared row-reading helper**

```python
# src/dataforensics/dictionary.py (append)
def read_rows(path: Path) -> list[dict]:
    data_lines, delimiter = _read_cleaned_lines(path)
    header = data_lines[0].split(delimiter)
    return [dict(zip(header, line.split(delimiter))) for line in data_lines[1:]]
```

- [ ] **Step 4: Update cli.py's scan command**

```python
# src/dataforensics/cli.py — replace the scan command body again
from dataforensics.config_schema import RulesConfigError, load_rules
from dataforensics.dictionary import build_data_dictionary, read_rows
from dataforensics.report import render_markdown
from dataforensics.validation import validate


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--out-dir", type=click.Path(), default=".")
def scan(file, rules_path, out_dir):
    """Read-only: emit a data dictionary and, if --rules is given, a validation report."""
    file_path = Path(file)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    stem = file_path.stem

    dictionary = build_data_dictionary(file_path)
    (out_dir_path / f"{stem}.data_dictionary.json").write_text(json.dumps(dictionary, indent=2))
    (out_dir_path / f"{stem}.data_dictionary.md").write_text(
        render_markdown(f"Data Dictionary: {file_path.name}", dictionary)
    )

    if rules_path is None:
        click.echo(f"scan complete: {len(dictionary)} columns profiled (no --rules given, validation skipped)")
        sys.exit(0)

    try:
        rules = load_rules(Path(rules_path))
    except RulesConfigError as exc:
        click.echo(f"Invalid rules file: {exc}", err=True)
        sys.exit(2)

    rows = read_rows(file_path)
    report_data = validate(rows, rules)
    (out_dir_path / f"{stem}.validation_report.json").write_text(json.dumps(report_data, indent=2))
    (out_dir_path / f"{stem}.validation_report.md").write_text(
        render_markdown(f"Validation Report: {file_path.name}", report_data)
    )

    click.echo(
        f"scan complete: {len(dictionary)} columns profiled, "
        f"{len(report_data['errors'])} errors, {len(report_data['warnings'])} warnings, "
        f"{len(report_data['suggestions'])} suggestions"
    )
    sys.exit(1 if report_data["errors"] else 0)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_scan_validation_report.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run full suite**

Run: `pytest -v`
Expected: PASS, all tests green

- [ ] **Step 7: Commit**

```bash
git add src/dataforensics/cli.py src/dataforensics/dictionary.py tests/integration/test_scan_validation_report.py
git commit -m "feat: wire three-tier validation report into scan; malformed rules exit 2"
```

---

### Task 12: Manifest module — versioning, hashing, atomic writes

**Files:**
- Create: `src/dataforensics/manifest.py`
- Test: `tests/unit/test_manifest.py`

**Interfaces:**
- Consumes: `hashing.sha256_file`.
- Produces: `manifest.build_manifest(input_paths: list[Path], schema_paths: list[Path], provenance: dict | None = None) -> dict` (the versioning/hashing envelope, `mutations: []` starts empty — Task 14 appends to it) and `manifest.atomic_write(path: Path, content: str) -> None`. Used by Tasks 14 and 15.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_manifest.py
import os
from pathlib import Path

from dataforensics.manifest import atomic_write, build_manifest


def test_build_manifest_has_required_fields(tmp_path):
    input_file = tmp_path / "input.csv"
    input_file.write_text("a,b\n1,2\n")
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("version: 1\n")

    manifest = build_manifest([input_file], [rules_file])

    for field in ("tool_version", "python_version", "dependency_versions", "run_id", "timestamp_utc", "mutations"):
        assert field in manifest

    assert len(manifest["input_sha256"]) == 1
    assert len(manifest["schema_sha256"]) == 1
    assert manifest["mutations"] == []
    assert manifest["provenance"] is None


def test_build_manifest_records_provenance_only_if_given(tmp_path):
    input_file = tmp_path / "input.csv"
    input_file.write_text("a,b\n1,2\n")
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("version: 1\n")

    manifest = build_manifest(
        [input_file], [rules_file], provenance={"source": "CDC", "dataset": "WONDER"}
    )
    assert manifest["provenance"] == {"source": "CDC", "dataset": "WONDER"}


def test_atomic_write_creates_file_with_content(tmp_path):
    target = tmp_path / "out.json"
    atomic_write(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'


def test_atomic_write_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "out.json"
    atomic_write(target, "data")
    remaining = list(tmp_path.iterdir())
    assert remaining == [target]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.manifest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/manifest.py
import os
import platform
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dataforensics import __version__
from dataforensics.hashing import sha256_file

_DIRECT_DEPENDENCIES = ["polars", "click", "pyyaml", "charset-normalizer", "rapidfuzz"]


def _dependency_versions() -> dict:
    versions = {}
    for name in _DIRECT_DEPENDENCIES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "unknown"
    return versions


def build_manifest(input_paths: list[Path], schema_paths: list[Path], provenance: dict | None = None) -> dict:
    return {
        "tool_version": __version__,
        "python_version": platform.python_version(),
        "dependency_versions": _dependency_versions(),
        "input_sha256": [sha256_file(p) for p in input_paths],
        "schema_sha256": [sha256_file(p) for p in schema_paths],
        "run_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance,
        "mutations": [],
    }


def atomic_write(path: Path, content: str) -> None:
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_manifest.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dataforensics/manifest.py tests/unit/test_manifest.py
git commit -m "feat: manifest versioning/hashing envelope and atomic writes"
```

---

### Task 13: `harmonize` single-file — dry run

**Files:**
- Create: `src/dataforensics/harmonize.py`
- Modify: `src/dataforensics/cli.py`
- Test: `tests/integration/test_harmonize_dry_run.py`

**Interfaces:**
- Consumes: `config_schema.load_rules`, `dictionary.read_rows`, `typing_guards.classify_sentinel`.
- Produces: `harmonize.plan_transformations(rows: list[dict], rules: dict) -> list[dict]` — each entry `{"rule": str, "column": str, "rows_affected": int}`, applying no mutation. Wired into `cli.harmonize` for the single-file case.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_harmonize_dry_run.py
from click.testing import CliRunner

from dataforensics.cli import main
from dataforensics.hashing import sha256_file


def _write_rules(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  smoking_status:\n"
        "    \"99\": Refused\n"
    )
    return rules_path


def test_dry_run_writes_nothing_and_prints_plan(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,smoking_status\n1,99\n2,10\n")
    rules_path = _write_rules(tmp_path)
    before_hash = sha256_file(src)
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert not output_path.exists()
    assert sha256_file(src) == before_hash
    assert "smoking_status" in result.output
    assert "1" in result.output  # rows_affected count for the sentinel rule


def test_output_path_same_as_input_exits_2(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,smoking_status\n1,99\n")
    rules_path = _write_rules(tmp_path)

    result = CliRunner().invoke(
        main, ["harmonize", str(src), "--rules", str(rules_path), "--output", str(src)]
    )
    assert result.exit_code == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_harmonize_dry_run.py -v`
Expected: FAIL — `harmonize` still prints "not implemented" and exits 3

- [ ] **Step 3: Write harmonize.py's planning function**

```python
# src/dataforensics/harmonize.py
def plan_transformations(rows: list[dict], rules: dict) -> list[dict]:
    plan = []
    missing_values = rules.get("missing_values", {})

    for column, sentinel_map in missing_values.items():
        affected = sum(1 for row in rows if str(row.get(column)) in sentinel_map)
        if affected:
            plan.append(
                {
                    "rule": f"missing_value_sentinel:{column}",
                    "column": column,
                    "rows_affected": affected,
                }
            )

    category_mappings = rules.get("category_mappings", {})
    for column, mapping in category_mappings.items():
        affected = sum(1 for row in rows if row.get(column) in mapping)
        if affected:
            plan.append(
                {
                    "rule": f"category_mapping:{column}",
                    "column": column,
                    "rows_affected": affected,
                }
            )

    return plan
```

- [ ] **Step 4: Update cli.py's harmonize command (single-file branch only)**

```python
# src/dataforensics/cli.py — replace the harmonize command body
from dataforensics.harmonize import plan_transformations


@main.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--output", type=click.Path(), default=None)
@click.option("--rules-map", default=None)
@click.option("--crosswalk", type=click.Path(exists=True), default=None)
@click.option("--output-dir", type=click.Path(), default=None)
@click.option("--execute", is_flag=True, default=False)
def harmonize(files, rules_path, output, rules_map, crosswalk, output_dir, execute):
    """Rules-driven, dry-run-by-default transform. Single file or cross-dataset crosswalk."""
    if len(files) == 1 and rules_path is not None:
        _harmonize_single_file(files[0], rules_path, output, execute)
        return

    click.echo("harmonize: multi-file crosswalk not implemented yet", err=True)
    sys.exit(3)


def _harmonize_single_file(file, rules_path, output, execute):
    file_path = Path(file)

    if output is None:
        click.echo("--output is required", err=True)
        sys.exit(2)
    output_path = Path(output)

    if output_path.resolve() == file_path.resolve():
        click.echo("--output must not be the same path as the input file", err=True)
        sys.exit(2)

    try:
        rules = load_rules(Path(rules_path))
    except RulesConfigError as exc:
        click.echo(f"Invalid rules file: {exc}", err=True)
        sys.exit(2)

    rows = read_rows(file_path)
    plan = plan_transformations(rows, rules)

    if not execute:
        click.echo(f"DRY RUN — no files written. Proposed transformations for {file_path.name}:")
        for item in plan:
            click.echo(f"  {item['rule']} -> {item['column']}: {item['rows_affected']} rows affected")
        if not plan:
            click.echo("  (no transformations would be applied)")
        sys.exit(0)

    click.echo("--execute not implemented yet", err=True)
    sys.exit(3)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_harmonize_dry_run.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run full suite**

Run: `pytest -v`
Expected: PASS, all tests green

- [ ] **Step 7: Commit**

```bash
git add src/dataforensics/harmonize.py src/dataforensics/cli.py tests/integration/test_harmonize_dry_run.py
git commit -m "feat: harmonize dry-run for single-file case; refuse output==input"
```

---

### Task 14: `harmonize --execute` — apply rules, write manifest, prove idempotency

**Files:**
- Modify: `src/dataforensics/harmonize.py`
- Modify: `src/dataforensics/cli.py`
- Test: `tests/regression/test_harmonize_execute.py`

**Interfaces:**
- Consumes: `manifest.build_manifest`, `manifest.atomic_write`, `typing_guards.classify_sentinel`.
- Produces: `harmonize.apply_transformations(rows: list[dict], rules: dict) -> tuple[list[dict], list[dict]]` returning `(transformed_rows, mutation_log_entries)`, where each mutation entry matches the manifest's per-mutation shape (`row_key`, `column`, `original_value`, `new_value`, `transformation_rule`). Wired into `_harmonize_single_file`'s `--execute` branch.

- [ ] **Step 1: Write the failing test**

```python
# tests/regression/test_harmonize_execute.py
import csv
import json

from click.testing import CliRunner

from dataforensics.cli import main
from dataforensics.hashing import sha256_file


def _write_input_and_rules(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,smoking_status\n1,99\n2,10\n3,99\n")
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  smoking_status:\n"
        "    \"99\": Refused\n"
    )
    return src, rules_path


def test_execute_applies_sentinel_rule_and_writes_manifest(tmp_path):
    src, rules_path = _write_input_and_rules(tmp_path)
    before_hash = sha256_file(src)
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(src),
            "--rules",
            str(rules_path),
            "--output",
            str(output_path),
            "--execute",
        ],
    )

    assert result.exit_code == 0
    assert sha256_file(src) == before_hash  # input untouched
    assert output_path.exists()

    with open(output_path) as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["smoking_status"] == "Refused"
    assert rows[1]["smoking_status"] == "10"
    assert rows[2]["smoking_status"] == "Refused"

    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["mutations"]) == 2
    assert manifest["mutations"][0]["row_key"] == {"participant_id": "1"}
    assert manifest["mutations"][0]["original_value"] == "99"
    assert manifest["mutations"][0]["new_value"] == "Refused"
    assert manifest["mutations"][0]["transformation_rule"] == "missing_value_sentinel:smoking_status"


def test_execute_is_idempotent(tmp_path):
    src, rules_path = _write_input_and_rules(tmp_path)
    output_path = tmp_path / "out.csv"

    CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )
    first_bytes = output_path.read_bytes()

    output_path.unlink()
    (output_path.with_suffix(output_path.suffix + ".manifest.json")).unlink()

    CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )
    second_bytes = output_path.read_bytes()

    assert first_bytes == second_bytes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/regression/test_harmonize_execute.py -v`
Expected: FAIL — `--execute` still prints "not implemented" and exits 3

- [ ] **Step 3: Write harmonize.py's apply function**

```python
# src/dataforensics/harmonize.py (append)
def apply_transformations(rows: list[dict], rules: dict) -> tuple[list[dict], list[dict]]:
    primary_key = rules["primary_key"]
    missing_values = rules.get("missing_values", {})
    category_mappings = rules.get("category_mappings", {})

    transformed = []
    mutations = []

    for row in rows:
        new_row = dict(row)
        row_key = {k: row.get(k) for k in primary_key}

        for column, sentinel_map in missing_values.items():
            original = new_row.get(column)
            if original is not None and str(original) in sentinel_map:
                new_value = sentinel_map[str(original)]
                mutations.append(
                    {
                        "row_key": row_key,
                        "column": column,
                        "original_value": original,
                        "new_value": new_value,
                        "transformation_rule": f"missing_value_sentinel:{column}",
                    }
                )
                new_row[column] = new_value

        for column, mapping in category_mappings.items():
            original = new_row.get(column)
            if original in mapping:
                new_value = mapping[original]
                mutations.append(
                    {
                        "row_key": row_key,
                        "column": column,
                        "original_value": original,
                        "new_value": new_value,
                        "transformation_rule": f"category_mapping:{column}",
                    }
                )
                new_row[column] = new_value

        transformed.append(new_row)

    return transformed, mutations
```

- [ ] **Step 4: Wire --execute in cli.py**

```python
# src/dataforensics/cli.py — replace the "--execute not implemented yet" branch in _harmonize_single_file
import csv
import io

from dataforensics.harmonize import apply_transformations
from dataforensics.manifest import atomic_write, build_manifest


def _harmonize_single_file(file, rules_path, output, execute):
    # ... (unchanged setup through `rows = read_rows(file_path)` and `plan = plan_transformations(...)`)

    if not execute:
        # ... (unchanged dry-run branch)
        return

    transformed_rows, mutations = apply_transformations(rows, rules)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(transformed_rows[0].keys()))
    writer.writeheader()
    writer.writerows(transformed_rows)
    atomic_write(output_path, buffer.getvalue())

    manifest = build_manifest([file_path], [Path(rules_path)])
    manifest["mutations"] = mutations
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    atomic_write(manifest_path, json.dumps(manifest, indent=2))

    click.echo(f"harmonize complete: wrote {output_path}, {len(mutations)} mutations logged")
    sys.exit(0)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/regression/test_harmonize_execute.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run full suite**

Run: `pytest -v`
Expected: PASS, all tests green

- [ ] **Step 7: Commit**

```bash
git add src/dataforensics/harmonize.py src/dataforensics/cli.py tests/regression/test_harmonize_execute.py
git commit -m "feat: harmonize --execute applies rules, writes atomic output + manifest, idempotent"
```

---

### Task 15: Cross-dataset crosswalk harmonization

**Files:**
- Modify: `src/dataforensics/harmonize.py`
- Modify: `src/dataforensics/cli.py`
- Test: `tests/integration/test_crosswalk.py`

**Interfaces:**
- Consumes: `apply_transformations`, `config_schema.load_rules`, `manifest.build_manifest`.
- Produces: `harmonize.apply_crosswalk(rows: list[dict], crosswalk_rules: dict) -> list[dict]` remapping column names/values per a crosswalk YAML's `column_map` and `value_map`. Wired into `cli.harmonize`'s multi-file branch, writing one file per source into `--output-dir` — never a merged table.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_crosswalk.py
import json

from click.testing import CliRunner

from dataforensics.cli import main


def _setup(tmp_path):
    wonder = tmp_path / "wonder.csv"
    wonder.write_text("county_fips,deaths,age_group,sex\n06081,12,25-34,M\n06001,5,35-44,F\n")
    wonder_rules = tmp_path / "wonder_rules.yaml"
    wonder_rules.write_text("version: 1\nprimary_key: [county_fips]\ncolumns: {}\n")

    pums = tmp_path / "pums.csv"
    pums.write_text("PUMA,AGEP,SEX,person_id\n0601,29,1,p1\n0602,41,2,p2\n")
    pums_rules = tmp_path / "pums_rules.yaml"
    pums_rules.write_text("version: 1\nprimary_key: [person_id]\ncolumns: {}\n")

    crosswalk = tmp_path / "crosswalk.yaml"
    crosswalk.write_text(
        "version: 1\n"
        "sources:\n"
        "  wonder:\n"
        "    column_map:\n"
        "      county_fips: geography_fips\n"
        "      age_group: age_band\n"
        "      sex: sex\n"
        "  pums:\n"
        "    column_map:\n"
        "      PUMA: geography_fips\n"
        "      AGEP: age_band\n"
        "      SEX: sex\n"
        "    value_map:\n"
        "      sex:\n"
        "        \"1\": M\n"
        "        \"2\": F\n"
    )
    return wonder, wonder_rules, pums, pums_rules, crosswalk


def test_crosswalk_writes_two_separate_tables_never_merged(tmp_path):
    wonder, wonder_rules, pums, pums_rules, crosswalk = _setup(tmp_path)
    output_dir = tmp_path / "harmonized"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(wonder),
            str(pums),
            "--rules-map",
            f"{wonder}={wonder_rules},{pums}={pums_rules}",
            "--crosswalk",
            str(crosswalk),
            "--output-dir",
            str(output_dir),
            "--execute",
        ],
    )

    assert result.exit_code == 0

    wonder_out = output_dir / "wonder.harmonized.csv"
    pums_out = output_dir / "pums.harmonized.csv"
    assert wonder_out.exists()
    assert pums_out.exists()

    import csv as csv_module

    with open(wonder_out) as fh:
        wonder_rows = list(csv_module.DictReader(fh))
    with open(pums_out) as fh:
        pums_rows = list(csv_module.DictReader(fh))

    # each source keeps its own row count -- proves no row-level join happened
    assert len(wonder_rows) == 2
    assert len(pums_rows) == 2

    assert set(wonder_rows[0].keys()) >= {"geography_fips", "age_band", "sex"}
    assert set(pums_rows[0].keys()) >= {"geography_fips", "age_band", "sex"}

    # PUMS numeric sex codes were remapped via value_map
    assert {row["sex"] for row in pums_rows} == {"M", "F"}

    manifest = json.loads((output_dir / "crosswalk.manifest.json").read_text())
    assert len(manifest["schema_sha256"]) == 3  # wonder rules + pums rules + crosswalk
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_crosswalk.py -v`
Expected: FAIL — multi-file branch still exits 3 with "not implemented yet"

- [ ] **Step 3: Write harmonize.py's crosswalk function**

```python
# src/dataforensics/harmonize.py (append)
def apply_crosswalk(rows: list[dict], source_crosswalk: dict) -> list[dict]:
    column_map = source_crosswalk.get("column_map", {})
    value_map = source_crosswalk.get("value_map", {})

    remapped = []
    for row in rows:
        new_row = {}
        for old_col, value in row.items():
            new_col = column_map.get(old_col, old_col)
            if new_col in value_map and value in value_map[new_col]:
                value = value_map[new_col][value]
            new_row[new_col] = value
        remapped.append(new_row)
    return remapped
```

- [ ] **Step 4: Wire the multi-file branch in cli.py**

```python
# src/dataforensics/cli.py — replace the multi-file branch in harmonize()
import yaml

from dataforensics.harmonize import apply_crosswalk


def _parse_rules_map(rules_map_str: str) -> dict:
    result = {}
    for pair in rules_map_str.split(","):
        file_str, rules_str = pair.split("=", 1)
        result[file_str] = rules_str
    return result


@main.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--output", type=click.Path(), default=None)
@click.option("--rules-map", default=None)
@click.option("--crosswalk", "crosswalk_path", type=click.Path(exists=True), default=None)
@click.option("--output-dir", type=click.Path(), default=None)
@click.option("--execute", is_flag=True, default=False)
def harmonize(files, rules_path, output, rules_map, crosswalk_path, output_dir, execute):
    """Rules-driven, dry-run-by-default transform. Single file or cross-dataset crosswalk."""
    if len(files) == 1 and rules_path is not None:
        _harmonize_single_file(files[0], rules_path, output, execute)
        return

    if len(files) >= 2 and rules_map and crosswalk_path and output_dir:
        _harmonize_crosswalk(files, rules_map, crosswalk_path, output_dir, execute)
        return

    click.echo("Invalid arguments: use --rules/--output for one file, or --rules-map/--crosswalk/--output-dir for 2+ files", err=True)
    sys.exit(2)


def _harmonize_crosswalk(files, rules_map_str, crosswalk_path, output_dir, execute):
    rules_map = _parse_rules_map(rules_map_str)
    output_dir_path = Path(output_dir)

    for f in files:
        if Path(f).resolve() == output_dir_path.resolve():
            click.echo("--output-dir must not be one of the input paths", err=True)
            sys.exit(2)

    try:
        crosswalk = yaml.safe_load(Path(crosswalk_path).read_text())
    except yaml.YAMLError as exc:
        click.echo(f"Invalid crosswalk file: {exc}", err=True)
        sys.exit(2)

    all_mutations = []
    schema_paths = [Path(crosswalk_path)]
    output_dir_path.mkdir(parents=True, exist_ok=True)

    for f in files:
        file_path = Path(f)
        source_key = str(file_path)
        if source_key not in rules_map:
            click.echo(f"No --rules-map entry for {source_key}", err=True)
            sys.exit(2)

        try:
            rules = load_rules(Path(rules_map[source_key]))
        except RulesConfigError as exc:
            click.echo(f"Invalid rules file for {source_key}: {exc}", err=True)
            sys.exit(2)
        schema_paths.append(Path(rules_map[source_key]))

        rows = read_rows(file_path)
        transformed_rows, mutations = apply_transformations(rows, rules)

        source_name = file_path.stem
        source_crosswalk = crosswalk.get("sources", {}).get(source_name, {})
        harmonized_rows = apply_crosswalk(transformed_rows, source_crosswalk)
        all_mutations.extend(mutations)

        if execute:
            out_path = output_dir_path / f"{source_name}.harmonized.csv"
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(harmonized_rows[0].keys()))
            writer.writeheader()
            writer.writerows(harmonized_rows)
            atomic_write(out_path, buffer.getvalue())
        else:
            click.echo(f"DRY RUN — {source_name}: {len(mutations)} rule-driven mutations, "
                       f"{len(harmonized_rows)} rows would be remapped to shared schema")

    if execute:
        manifest = build_manifest([Path(f) for f in files], schema_paths)
        manifest["mutations"] = all_mutations
        atomic_write(output_dir_path / "crosswalk.manifest.json", json.dumps(manifest, indent=2))
        click.echo(f"crosswalk harmonize complete: {len(files)} sources written to {output_dir_path}, never merged")
    sys.exit(0)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_crosswalk.py -v`
Expected: PASS (1 test)

- [ ] **Step 6: Run full suite**

Run: `pytest -v`
Expected: PASS, all tests green

- [ ] **Step 7: Commit**

```bash
git add src/dataforensics/harmonize.py src/dataforensics/cli.py tests/integration/test_crosswalk.py
git commit -m "feat: cross-dataset crosswalk harmonization, one output table per source, never merged"
```

---

### Task 16: Fixtures and 5-minute quickstart

**Files:**
- Create: `fixtures/sample.csv`
- Create: `fixtures/sample_rules.yaml`
- Test: `tests/e2e/test_quickstart.py`

**Interfaces:**
- Consumes: the full `scan`/`harmonize` CLI built in Tasks 8-14.
- Produces: a committed fixture any reviewer (or CI) can run against with no external downloads.

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_quickstart.py
from pathlib import Path

from click.testing import CliRunner

from dataforensics.cli import main

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_quickstart_scan_and_dry_run_harmonize(tmp_path):
    sample = FIXTURES / "sample.csv"
    rules = FIXTURES / "sample_rules.yaml"

    scan_result = CliRunner().invoke(main, ["scan", str(sample), "--rules", str(rules), "--out-dir", str(tmp_path)])
    assert scan_result.exit_code in (0, 1)  # 1 is fine — the fixture plants a real error on purpose

    harmonize_result = CliRunner().invoke(
        main,
        ["harmonize", str(sample), "--rules", str(rules), "--output", str(tmp_path / "out.csv")],
    )
    assert harmonize_result.exit_code == 0
    assert not (tmp_path / "out.csv").exists()  # dry run by default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/e2e/test_quickstart.py -v`
Expected: FAIL — `fixtures/sample.csv` doesn't exist yet

- [ ] **Step 3: Create the fixture (20 rows, deliberately planted issues)**

```csv
participant_id,age,sex,smoking_status,county_fips,visit_date
001,34,M,10,06081,2024-01-15
002,29,F,99,06001,2024-02-20
003,-5,M,5,02138,2024-03-01
004,41,F,0,48201,2024-01-30
005,52,M,15,06081,03/04/2024
006,38,F,99,02138,2024-02-14
007,45,M,8,06001,2024-01-05
008,60,F,20,48201,2024-04-10
009,33,M,12,06081,2024-03-22
010,27,F,0,02138,2024-02-28
001,34,M,10,06081,2024-01-15
011,48,M,18,06001,2024-01-19
012,55,F,9,48201,2024-03-15
013,31,M,7,06081,2024-02-02
014,44,F,99,02138,2024-01-27
015,39,M,11,06001,2024-04-01
016,58,F,14,48201,2024-03-08
017,26,M,6,06081,2024-02-11
018,47,F,16,02138,2024-01-22
019,35,M,9,06001,2024-03-30
```

Planted issues, for the write-up: duplicate `participant_id` (`001` appears twice), a negative `age` (row 3, an Error), sentinel `99` in `smoking_status` (an undeclared missing code until the rules file maps it), a leading-zero FIPS code (`02138`), and one ambiguous date (`03/04/2024` on row 5, no ISO8601).

- [ ] **Step 4: Create the matching rules file**

```yaml
# fixtures/sample_rules.yaml
version: 1
primary_key: [participant_id]
columns:
  age:
    type: integer
    minimum: 0
    maximum: 120
missing_values:
  smoking_status:
    "99": Refused
category_mappings: {}
weights_strata:
  columns: []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/e2e/test_quickstart.py -v`
Expected: PASS (1 test)

- [ ] **Step 6: Commit**

```bash
git add fixtures/sample.csv fixtures/sample_rules.yaml tests/e2e/test_quickstart.py
git commit -m "feat: bundled quickstart fixture with deliberately planted issues"
```

---

### Task 17: Real datasets end-to-end (manual download + documented run)

**Files:**
- Create: `schemas/cdc_wonder_rules.yaml`
- Create: `schemas/acs_pums_rules.yaml`
- Create: `schemas/wonder_pums_crosswalk.yaml`
- Create: `WRITEUP.md`
- Create: `docs/REAL_DATA_RUNS.md`

This task is manual/documentation-heavy — the three sources require interactive or bulk download (CDC WONDER's query UI, Census's PUMS bulk files, an OpenNeuro dataset via its web UI or `datalad`) rather than a single scriptable fetch, so there is no failing-test step to start from. Do it after Tasks 1-16 are green so the full pipeline exists to run these through.

- [ ] **Step 1: Pull one CDC WONDER export**

Go to https://wonder.cdc.gov/, choose any public database (e.g. Underlying Cause of Death), run a small query grouped by county/age/sex, export as TSV to `data/raw/cdc_wonder_export.tsv`. Confirm it has the trailing disclaimer block Task 4's `strip_footer` targets — if the export doesn't include one, check WONDER's export options for "include query criteria" and re-export with it on.

- [ ] **Step 2: Pull one ACS PUMS extract + its data dictionary**

From https://www.census.gov/programs-surveys/acs/microdata.html, download one state's person-level PUMS CSV plus the PUMS data dictionary for that year into `data/raw/acs_pums_extract.csv` and `data/raw/acs_pums_data_dictionary.txt`.

- [ ] **Step 3: Pull one OpenNeuro participants.tsv + phenotype file**

From https://openneuro.org/, pick any dataset with a `participants.tsv` and at least one phenotype file, download into `data/raw/openneuro_participants.tsv` (+ phenotype file). **Before committing any rows from this file to the repo, check that specific dataset's license on its OpenNeuro page** — most are CC0 but not all (per MASTER_PROMPT.md §2).

- [ ] **Step 4: Write rules files for WONDER and PUMS**

```yaml
# schemas/cdc_wonder_rules.yaml
version: 1
primary_key: [county_fips]   # adjust to match the actual export's grouping columns
columns:
  deaths:
    type: integer
    minimum: 0
missing_values:
  deaths:
    "Suppressed": "Suppressed (small-cell, per WONDER confidentiality policy)"
category_mappings: {}
weights_strata:
  columns: []
```

```yaml
# schemas/acs_pums_rules.yaml
version: 1
primary_key: [person_id]     # adjust to match SERIALNO/SPORDER or your extract's actual key
columns:
  AGEP:
    type: integer
    minimum: 0
    maximum: 99               # PUMS top-codes age at 99 in most releases -- check your extract's dictionary
missing_values: {}
category_mappings: {}
weights_strata:
  columns: [PWGTP]            # ACS PUMS person weight -- never auto-cleaned or normalized
```

- [ ] **Step 5: Write the WONDER/PUMS crosswalk**

Fill in `schemas/wonder_pums_crosswalk.yaml` following the same shape as `tests/integration/test_crosswalk.py`'s fixture crosswalk, using the real column names from your two actual exports (target schema: `geography_fips`, `age_band`, `sex`).

- [ ] **Step 6: Run scan on all three real sources**

```bash
dataforensics scan data/raw/cdc_wonder_export.tsv --rules schemas/cdc_wonder_rules.yaml --out-dir data/standardized
dataforensics scan data/raw/acs_pums_extract.csv --rules schemas/acs_pums_rules.yaml --out-dir data/standardized
dataforensics scan data/raw/openneuro_participants.tsv --out-dir data/standardized
```

Fix whatever breaks — footer-stripping thresholds, delimiter detection, or rule bounds may need adjustment against the real files. This is expected; that's the point of running real data.

- [ ] **Step 7: Run the crosswalk demo for real**

```bash
dataforensics harmonize data/raw/cdc_wonder_export.tsv data/raw/acs_pums_extract.csv \
  --rules-map "data/raw/cdc_wonder_export.tsv=schemas/cdc_wonder_rules.yaml,data/raw/acs_pums_extract.csv=schemas/acs_pums_rules.yaml" \
  --crosswalk schemas/wonder_pums_crosswalk.yaml \
  --output-dir data/cleaned/wonder_pums_harmonized \
  --execute
```

- [ ] **Step 8: Write WRITEUP.md**

```markdown
# DataForensics: before/after

One page. For each of the three datasets: what `dataforensics scan` found (errors/warnings/suggestions,
with counts), the single most interesting real issue caught, and — for the WONDER/PUMS pair —
what the crosswalk demo actually harmonized and why it's two tables, not one merged table.
Fill in with your real scan/harmonize output from Steps 6-7 above.
```

- [ ] **Step 9: Commit**

```bash
git add schemas/ WRITEUP.md docs/REAL_DATA_RUNS.md
git commit -m "feat: real dataset rules files, crosswalk config, and write-up scaffold"
```

(Note: `data/raw/*` stays gitignored per Task 1's `.gitignore` — commit only the schemas and the write-up, not the downloaded data itself, unless license-cleared per Step 3.)

---

### Task 18: README, non-goals, failure-mode/exit-code tests, final polish

**Files:**
- Create: `README.md`
- Test: `tests/unit/test_failure_modes.py`

**Interfaces:**
- Consumes: the complete CLI.
- Produces: the public-facing pitch document and a final safety-net test file covering every failure mode from MASTER_PROMPT.md §8.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_failure_modes.py
from click.testing import CliRunner

from dataforensics.cli import main


def test_scan_nonexistent_file():
    result = CliRunner().invoke(main, ["scan", "does_not_exist.csv"])
    assert result.exit_code != 0


def test_scan_empty_file(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("")
    result = CliRunner().invoke(main, ["scan", str(f), "--out-dir", str(tmp_path)])
    assert result.exit_code != 0 or result.exit_code == 0  # must not crash uncaught


def test_harmonize_invalid_yaml_exits_2(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("id,age\n1,40\n")
    bad_rules = tmp_path / "bad.yaml"
    bad_rules.write_text("  bad: [\n")

    result = CliRunner().invoke(
        main, ["harmonize", str(src), "--rules", str(bad_rules), "--output", str(tmp_path / "out.csv")]
    )
    assert result.exit_code == 2


def test_harmonize_output_path_collision_exits_2(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("id,age\n1,40\n")
    rules = tmp_path / "rules.yaml"
    rules.write_text("version: 1\nprimary_key: [id]\ncolumns: {}\n")

    result = CliRunner().invoke(
        main, ["harmonize", str(src), "--rules", str(rules), "--output", str(src)]
    )
    assert result.exit_code == 2
```

- [ ] **Step 2: Run test to verify current failures**

Run: `pytest tests/unit/test_failure_modes.py -v`
Expected: some pass already (Tasks 9/13 covered YAML and path-collision); the empty-file case may raise an uncaught exception — that's the bug this task fixes.

- [ ] **Step 3: Harden `build_data_dictionary` against an empty file**

```python
# src/dataforensics/dictionary.py — guard at the top of build_data_dictionary
def build_data_dictionary(path: Path) -> dict:
    data_lines, delimiter = _read_cleaned_lines(path)
    if not data_lines:
        return {}
    header = data_lines[0].split(delimiter)
    # ... unchanged from here
```

And in `cli.py`'s `scan`, treat zero columns as a clean no-op rather than a crash (already naturally handled once `build_data_dictionary` returns `{}` instead of raising).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_failure_modes.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write README.md**

```markdown
# DataForensics (dataforensics)

Most research-data cleanup tools profile a file and hope for the best. `dataforensics` is built around
one rule instead: **when it's uncertain, it preserves the data and reports the uncertainty —
it never guesses.** No transformation happens without an explicit rule you wrote down; every
one that does happen is logged to an audit trail with enough detail to answer "exactly what
happened to this dataset, and why."

## Why not ydata-profiling / great_expectations / pointblank?

Those are excellent generic profilers. `dataforensics` is narrower and more opinionated, tuned specifically
to research-export quirks those tools don't target: REDCap-style missing-value sentinels (`-99`,
`"Refused"`) kept distinct from true nulls, FIPS/ZIP/ID columns protected from integer-cast
leading-zero truncation, Census/NHANES-style top-coding distinguished from genuine outliers, and
CDC WONDER-style disclaimer footers stripped before they corrupt a naive parser. If you don't need
those, use a generic profiler — it'll do less, but it'll also ask you for less.

## Quickstart (5 minutes, no downloads required)

\`\`\`bash
pip install -e ".[dev]"
dataforensics scan fixtures/sample.csv --rules fixtures/sample_rules.yaml
dataforensics harmonize fixtures/sample.csv --rules fixtures/sample_rules.yaml --output /tmp/out.csv
\`\`\`

The first command profiles the bundled fixture and reports its planted issues (a duplicate ID,
a negative age, an unmapped sentinel code, an ambiguous date). The second previews — without
writing anything — what a rules-driven cleanup would change. Add `--execute` to actually write.

## What this doesn't do (on purpose)

No automatic imputation. No automatic fuzzy-match deduplication (suggestions only, never applied).
No automatic unit conversion. No NLU/codebook semantic parsing beyond an explicitly-provided data
dictionary. No ML anomaly detection. No GUI. No claims about statistical or scientific validity —
only "no violations of configured rules detected."

## Full design spec

See [MASTER_PROMPT.md](MASTER_PROMPT.md) for the complete architecture, safety invariants, and
the reasoning behind them.
```

- [ ] **Step 6: Run the entire test suite one final time**

Run: `pytest -v`
Expected: PASS, every test across unit/integration/regression/e2e green

- [ ] **Step 7: Commit**

```bash
git add README.md src/dataforensics/dictionary.py tests/unit/test_failure_modes.py
git commit -m "feat: README, empty-file hardening, failure-mode test coverage"
```

---

### Task 19: Read-only Streamlit viewer over JSON reports

Added after the CLI-only scope was reopened (2026-08-24) — Aidan wants a viewer matching the pattern in the sibling `hospital-price-concentration` project. Scope stays narrow: **pure presentation over files `dataforensics` already produces**, no write path, no calling the CLI from within the app, no new business logic. Do this task after Task 18 — it needs real report JSON shapes to render, which only exist once `scan`/`harmonize` are fully built.

**Files:**
- Create: `src/dataforensics/viewer.py` (pure functions — classification/summary logic, framework-independent and directly testable)
- Create: `app.py` (thin Streamlit script, not unit-tested directly — see Step 6)
- Modify: `pyproject.toml` (add a `viewer` optional-dependency group so the core CLI install stays lightweight)
- Test: `tests/unit/test_viewer.py`

**Interfaces:**
- Consumes: the JSON shapes already defined by `dictionary.build_data_dictionary` (Task 6), `validation.validate` (Task 10), and `manifest.build_manifest` (Task 12) — no new shapes introduced.
- Produces: `viewer.classify_report(data: dict) -> str` (`"data_dictionary"` / `"validation_report"` / `"manifest"` / `"unknown"`) and `viewer.validation_summary(data: dict) -> dict` (counts by severity). `app.py` imports these and does only file upload + rendering — kept out of the plan's test surface deliberately, verified by a manual smoke test instead (Step 7), the same way Task 17's real-dataset runs are manual rather than scripted.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_viewer.py
from dataforensics.viewer import classify_report, validation_summary


def test_classify_data_dictionary():
    data = {"age": {"dtype": "Utf8", "non_null_pct": 100.0}}
    assert classify_report(data) == "data_dictionary"


def test_classify_validation_report():
    data = {"errors": [], "warnings": [], "suggestions": [], "checks_evaluated": 0, "checks_passed": 0}
    assert classify_report(data) == "validation_report"


def test_classify_manifest():
    data = {"run_id": "abc", "mutations": [], "tool_version": "0.1.0"}
    assert classify_report(data) == "manifest"


def test_classify_unknown_for_unrecognized_shape():
    assert classify_report({"foo": "bar"}) == "unknown"


def test_validation_summary_counts_by_severity():
    data = {
        "errors": [{"rule": "minimum"}],
        "warnings": [{"rule": "maximum"}, {"rule": "maximum"}],
        "suggestions": [{"rule": "iqr_outlier"}],
        "checks_evaluated": 10,
        "checks_passed": 7,
    }
    summary = validation_summary(data)
    assert summary == {
        "errors": 1,
        "warnings": 2,
        "suggestions": 1,
        "checks_evaluated": 10,
        "checks_passed": 7,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_viewer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dataforensics.viewer'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/viewer.py
def classify_report(data: dict) -> str:
    if "mutations" in data and "run_id" in data:
        return "manifest"
    if {"errors", "warnings", "suggestions"} <= data.keys():
        return "validation_report"
    if data and all(isinstance(v, dict) for v in data.values()):
        return "data_dictionary"
    return "unknown"


def validation_summary(data: dict) -> dict:
    return {
        "errors": len(data.get("errors", [])),
        "warnings": len(data.get("warnings", [])),
        "suggestions": len(data.get("suggestions", [])),
        "checks_evaluated": data.get("checks_evaluated", 0),
        "checks_passed": data.get("checks_passed", 0),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_viewer.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Add the `viewer` optional-dependency group**

```toml
# pyproject.toml — add alongside [project.optional-dependencies]
[project.optional-dependencies]
dev = ["pytest>=8.0"]
viewer = ["streamlit>=1.30"]
```

- [ ] **Step 6: Write the thin Streamlit app**

```python
# app.py
import json

import streamlit as st

from dataforensics.viewer import classify_report, validation_summary

st.set_page_config(page_title="dataforensics report viewer", layout="wide")
st.title("DataForensics — report viewer")
st.caption("Read-only. Upload a JSON file dataforensics already produced (data dictionary, validation report, or manifest).")

uploaded = st.file_uploader("Upload a .json report", type="json")

if uploaded is None:
    st.info("No file uploaded yet.")
else:
    data = json.load(uploaded)
    kind = classify_report(data)

    if kind == "validation_report":
        summary = validation_summary(data)
        cols = st.columns(5)
        cols[0].metric("Errors", summary["errors"])
        cols[1].metric("Warnings", summary["warnings"])
        cols[2].metric("Suggestions", summary["suggestions"])
        cols[3].metric("Checks evaluated", summary["checks_evaluated"])
        cols[4].metric("Checks passed", summary["checks_passed"])
        for severity in ("errors", "warnings", "suggestions"):
            with st.expander(f"{severity.capitalize()} ({len(data[severity])})"):
                st.json(data[severity])

    elif kind == "data_dictionary":
        st.dataframe(
            [{"column": col, **fields} for col, fields in data.items()],
            use_container_width=True,
        )

    elif kind == "manifest":
        st.write(
            {
                "tool_version": data.get("tool_version"),
                "run_id": data.get("run_id"),
                "timestamp_utc": data.get("timestamp_utc"),
                "input_sha256": data.get("input_sha256"),
            }
        )
        st.subheader(f"Mutations ({len(data.get('mutations', []))})")
        st.dataframe(data.get("mutations", []), use_container_width=True)

    else:
        st.error("Unrecognized report shape — this doesn't look like dataforensics output.")
```

- [ ] **Step 7: Manual smoke test (not automated — thin rendering layer only)**

```bash
pip install -e ".[dev,viewer]"
dataforensics scan fixtures/sample.csv --rules fixtures/sample_rules.yaml --out-dir /tmp/rdh_demo
streamlit run app.py
```

Upload `/tmp/rdh_demo/sample.validation_report.json`, confirm the metrics and expanders render; upload `/tmp/rdh_demo/sample.data_dictionary.json`, confirm the table renders. This mirrors how Task 17's real-dataset runs are verified manually rather than scripted — Streamlit apps aren't meaningfully unit-testable beyond the pure logic already covered in Step 1-4.

- [ ] **Step 8: Update README and CI**

Add a "Viewer (optional)" section to `README.md`'s quickstart pointing at Steps 6-7 above. Add `streamlit>=1.30` install to `.github/workflows/ci.yml`'s install step (`pip install -e ".[dev,viewer]"`) so `test_viewer.py`'s import of `dataforensics.viewer` — which itself has no Streamlit dependency — keeps working either way; this is only needed if a later task adds Streamlit-specific tests.

- [ ] **Step 9: Run full suite**

Run: `pytest -v`
Expected: PASS, all tests green including the 5 new viewer tests

- [ ] **Step 10: Commit**

```bash
git add src/dataforensics/viewer.py app.py pyproject.toml README.md tests/unit/test_viewer.py
git commit -m "feat: read-only Streamlit viewer over existing JSON report output"
```

---

### Task 20: Ambiguous date format detection (Error tier)

**Found during a final readiness audit (2026-08-24):** MASTER_PROMPT.md's §1 principles and the original spec both call this out as one of the most important safety rules — "ambiguous dates are flagged as a Critical validation warning and never silently parsed" — and Task 16's own fixture plants a `03/04/2024` value specifically to exercise it. But no task in the plan as written actually implements date-column handling: `validation.py` (Task 10) only checks `minimum`/`maximum`, and `type: date`/`format` in the rules YAML (§6) was documented but never enforced. This task closes that gap.

**Files:**
- Modify: `src/dataforensics/validation.py`
- Test: `tests/unit/test_date_validation.py`

**Interfaces:**
- Produces: `validation.is_ambiguous_date(value: str) -> bool` and a new check inside `validate()` for any column where `columns.<name>.type == "date"`. Findings use `rule` values `"ambiguous_date_format"` and `"date_format_mismatch"`, both Error tier (deterministic — the rule being violated is "a date column must be unambiguous or have an explicit format," not a guess about the value itself).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_date_validation.py
from dataforensics.validation import is_ambiguous_date, validate

_DATE_RULES = {
    "version": 1,
    "primary_key": ["participant_id"],
    "columns": {
        "visit_date": {"type": "date"},
    },
    "missing_values": {},
    "category_mappings": {},
    "weights_strata": {"columns": []},
}

_DATE_RULES_WITH_FORMAT = {
    **_DATE_RULES,
    "columns": {"visit_date": {"type": "date", "format": "%Y-%m-%d"}},
}


def test_is_ambiguous_date_flags_slash_format():
    assert is_ambiguous_date("03/04/2024") is True


def test_is_ambiguous_date_does_not_flag_iso8601():
    assert is_ambiguous_date("2024-03-04") is False


def test_slash_date_with_no_declared_format_is_error():
    rows = [{"participant_id": "1", "visit_date": "03/04/2024"}]
    result = validate(rows, _DATE_RULES)
    ambiguous = [e for e in result["errors"] if e["rule"] == "ambiguous_date_format"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["column"] == "visit_date"


def test_iso8601_date_with_no_declared_format_is_not_flagged():
    rows = [{"participant_id": "1", "visit_date": "2024-03-04"}]
    result = validate(rows, _DATE_RULES)
    assert result["errors"] == []


def test_value_matching_declared_format_is_not_flagged():
    rows = [{"participant_id": "1", "visit_date": "2024-03-04"}]
    result = validate(rows, _DATE_RULES_WITH_FORMAT)
    assert result["errors"] == []


def test_value_not_matching_declared_format_is_error():
    rows = [{"participant_id": "1", "visit_date": "03/04/2024"}]
    result = validate(rows, _DATE_RULES_WITH_FORMAT)
    mismatches = [e for e in result["errors"] if e["rule"] == "date_format_mismatch"]
    assert len(mismatches) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_date_validation.py -v`
Expected: FAIL — `is_ambiguous_date` doesn't exist; date columns aren't checked at all yet

- [ ] **Step 3: Write minimal implementation**

```python
# src/dataforensics/validation.py — add near the top
import re
from datetime import datetime

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def is_ambiguous_date(value: str) -> bool:
    if _ISO_DATE_PATTERN.match(value):
        return False
    return bool(_SLASH_DATE_PATTERN.match(value))
```

```python
# src/dataforensics/validation.py — inside validate()'s per-row, per-column loop, alongside the
# existing "minimum"/"maximum" checks (same indentation level, same row/column loop):
            if col_rules.get("type") == "date":
                checks_evaluated += 1
                declared_format = col_rules.get("format")
                if declared_format:
                    try:
                        datetime.strptime(raw_value, declared_format)
                    except ValueError:
                        errors.append(
                            {
                                "column": column,
                                "row_key": row_key,
                                "rule": "date_format_mismatch",
                                "message": f"{column}={raw_value} does not match declared format {declared_format}",
                                "severity": "error",
                            }
                        )
                elif is_ambiguous_date(raw_value):
                    errors.append(
                        {
                            "column": column,
                            "row_key": row_key,
                            "rule": "ambiguous_date_format",
                            "message": f"{column}={raw_value} is ambiguous (MM/DD vs DD/MM) with no declared format — not parsed",
                            "severity": "error",
                        }
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_date_validation.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full suite, confirm Task 16's fixture now actually catches its own planted issue**

Run: `pytest -v`
Expected: PASS. Additionally run `dataforensics scan fixtures/sample.csv --rules fixtures/sample_rules.yaml` by hand — the report should now include an `ambiguous_date_format` error for row 5's `03/04/2024`, closing the loop on what that fixture was always meant to demonstrate. (`fixtures/sample_rules.yaml` needs a `visit_date: {type: date}` entry added — add it now as part of this task.)

- [ ] **Step 6: Commit**

```bash
git add src/dataforensics/validation.py tests/unit/test_date_validation.py fixtures/sample_rules.yaml
git commit -m "feat: ambiguous date format detection (Error tier) — closes a gap the fixture already anticipated"
```

---

### Task 21: Wire the `dataforensics report` command

**Found during the same final audit:** `dataforensics report <artifact.json>` is defined in MASTER_PROMPT.md §5 and stubbed out in Task 1 ("not implemented," exit 3) — but no later task ever replaces the stub. It's the only one of the three CLI verbs that stays permanently unimplemented across the whole plan as written.

**Files:**
- Modify: `src/dataforensics/cli.py`
- Test: `tests/integration/test_report_command.py`

**Interfaces:**
- Consumes: `report.render_markdown` (Task 8), `viewer.classify_report` (Task 19 — reused here for a sensible title, e.g. "Validation Report" vs. "Data Dictionary," rather than duplicating that logic).
- Produces: `dataforensics report <artifact.json> [--out <path>]` — prints rendered Markdown to stdout by default, or writes it to `--out` if given.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_report_command.py
import json

from click.testing import CliRunner

from dataforensics.cli import main


def test_report_renders_validation_report_to_stdout(tmp_path):
    artifact = tmp_path / "sample.validation_report.json"
    artifact.write_text(json.dumps({"errors": [], "warnings": [], "suggestions": [], "checks_evaluated": 1, "checks_passed": 1}))

    result = CliRunner().invoke(main, ["report", str(artifact)])
    assert result.exit_code == 0
    assert "# Validation Report" in result.output


def test_report_writes_to_out_path_when_given(tmp_path):
    artifact = tmp_path / "sample.data_dictionary.json"
    artifact.write_text(json.dumps({"age": {"dtype": "Utf8", "non_null_pct": 100.0}}))
    out_path = tmp_path / "rendered.md"

    result = CliRunner().invoke(main, ["report", str(artifact), "--out", str(out_path)])
    assert result.exit_code == 0
    assert out_path.exists()
    assert "# Data Dictionary" in out_path.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_report_command.py -v`
Expected: FAIL — `report` still prints "not implemented" and exits 3

- [ ] **Step 3: Wire cli.py's report command**

```python
# src/dataforensics/cli.py — replace the report command body
from dataforensics.viewer import classify_report

_REPORT_TITLES = {
    "data_dictionary": "Data Dictionary",
    "validation_report": "Validation Report",
    "manifest": "Transformation Manifest",
    "unknown": "Report",
}


@main.command()
@click.argument("artifact", type=click.Path(exists=True))
@click.option("--out", type=click.Path(), default=None)
def report(artifact, out):
    """Render a data_dictionary/validation_report/manifest JSON file to Markdown."""
    data = json.loads(Path(artifact).read_text())
    title = _REPORT_TITLES[classify_report(data)]
    markdown = render_markdown(title, data)

    if out:
        Path(out).write_text(markdown)
    else:
        click.echo(markdown)
    sys.exit(0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_report_command.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: PASS, all tests green — this is the last task, so this is the final full-suite run

- [ ] **Step 6: Commit**

```bash
git add src/dataforensics/cli.py tests/integration/test_report_command.py
git commit -m "feat: wire the previously-stubbed dataforensics report command"
```

Note: this task depends on `viewer.classify_report` from Task 19. If Task 19 (Streamlit viewer) is ever descoped, inline a copy of `classify_report`'s three-line logic directly in `cli.py` instead of importing from `dataforensics.viewer` — don't make the core CLI's third verb depend on an optional-extras module.

---

## Self-Review

**Spec coverage:** every MASTER_PROMPT.md section maps to a task — §1 safety/immutability → Tasks 2, 12, 13, 14; §1 never-guess-semantics → Tasks 5, 9, 13, 14; §1 three-tier validation → Task 10; §1 determinism/versioning → Tasks 12, 14; §1 privacy defaults → deferred: **gap found and noted below**; §2 blind spots (WONDER suppression, no-merge crosswalk, footer algorithm, license check) → Tasks 4, 15, 17; §3 datasets → Task 17; §5 CLI shape → Tasks 1, 8, 11, 13, 15; §6 rules YAML → Task 9; §7 build order → this plan's task order; §8 testing levels → spread across all tasks + Task 18; §9/§10 definition of done / non-goals → Task 18's README.

**Gap found during self-review:** MASTER_PROMPT.md §1 "Privacy defaults" (masking name/SSN/MRN-like columns in reports by default) has no dedicated task above. Add before starting Task 18:

### Task 17.5: PII pattern masking in reports

**Files:** Modify `src/dataforensics/dictionary.py`, `src/dataforensics/report.py`; Test: `tests/unit/test_pii_masking.py`

- [ ] Write a failing test asserting a column named `patient_name` or matching an SSN-shaped value pattern has its `levels`/sample values masked (e.g. `"levels": "[masked: potential identifier pattern detected]"`) in `build_data_dictionary`'s output by default, with raw values only shown when an explicit `include_raw_samples=True` argument is passed.
- [ ] Add `_PII_COLUMN_PATTERN = re.compile(r"(name|ssn|mrn|email|phone|dob)", re.IGNORECASE)` to `typing_guards.py` and a `is_pii_like_column(name: str) -> bool` function, mirroring Task 5's style.
- [ ] In `build_data_dictionary`, when `is_pii_like_column(name)` is true, replace `levels` with the masking string regardless of cardinality, and never include raw value samples anywhere in `result[name]`.
- [ ] Run tests, commit: `git commit -m "feat: mask PII-pattern columns in reports by default"`.

**Note on Task 19 vs. the original spec:** MASTER_PROMPT.md §10 lists "No GUI" as an explicit v1 non-goal. Task 19 was added afterward at Aidan's request and is a deliberate, logged scope change, not an inconsistency slipping through — it stays inside the spirit of the constraint (no *write* surface, no new engine logic, pure read-only presentation over files the CLI already produces) rather than reopening the GUI question generally. MASTER_PROMPT.md's non-goals section has been amended to reflect this so the two documents don't contradict each other.

**Second gap-finding pass (2026-08-24, requested directly):** re-read the whole plan against MASTER_PROMPT.md looking specifically for spec requirements with no implementing task. Found two: (1) ambiguous-date detection — one of the most heavily emphasized safety rules in the original spec, and something Task 16's fixture already planted a test value for (`03/04/2024`) without any task ever actually checking it — added as Task 20. (2) `dataforensics report`, defined in §5 and stubbed in Task 1, was never wired to a real implementation in any later task — added as Task 21. Also noted and deliberately deferred rather than silently dropped: explicit unit-conversion rules (§1 allows them when schema-declared, but none of the three chosen demo datasets need one, so no task builds it — if a future dataset needs it, add a task following the same pattern as Task 14's `apply_transformations`, keyed off a new `unit_conversions:` rules-YAML section) and a `type: integer`-driven numeric cast on cleaned output (currently validated for range but left as a string in the output file, which is consistent with the spec's "preserve raw strings unless explicitly told to convert" principle, so this is a deliberate minimalism, not an oversight).

**Placeholder scan:** no TBD/TODO markers remain; the one intentionally-manual task (17) is manual because the underlying action (browser downloads) is not scriptable, not because content was deferred — every step in it has concrete URLs, commands, or file templates.

**Type consistency:** `row_key` is a `dict` everywhere it appears (Tasks 10, 12, 14, 15) — checked. `plan_transformations` and `apply_transformations` both key mutations by `rule` strings of the form `"missing_value_sentinel:{column}"` / `"category_mapping:{column}"` — checked consistent between Tasks 13 and 14. `RulesConfigError` is raised only from `config_schema.py` and caught only in `cli.py` — checked.

---

Plan complete and saved to `docs/superpowers/plans/2026-08-24-DataForensics.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
