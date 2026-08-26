# JSON & Excel Input Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DataForensics (CLI + Streamlit app) accepts `.json` and `.xlsx`/`.xls` input alongside CSV/TSV, producing identical `data_dictionary`/`validation_report`/harmonize output to the equivalent CSV.

**Architecture:** Everything downstream of ingestion (validation, harmonize, investigate) already runs on a format-agnostic `list[dict[str, str]]`. This plan adds two new producers of that shape in `ingest.py` (`read_json_rows`, `read_excel_rows`) behind a `detect_file_format()` router, then makes `dictionary.py`'s `read_rows`/`build_data_dictionary`, `cli.py`'s commands, and `app.py`'s upload widgets dispatch through it. The existing delimited-text (CSV/TSV) code path is untouched line-for-line.

**Tech Stack:** Python 3.11+, stdlib `json` for JSON, `openpyxl` for `.xlsx`, `xlrd` for legacy `.xls`. No pandas (project-wide rule — Polars is the only tabular dependency, and this feature does not need one either).

## Global Constraints

- No pandas, anywhere (existing project rule — see README's "Why not ydata-profiling" section; this project already avoids pandas throughout).
- `IngestFormatError` (new) and `DuplicateHeaderError` (existing) both map to CLI exit code 3 — "malformed input file," never a config error (exit 2) or silent success.
- Never guess: a JSON shape other than "array of flat objects," a multi-sheet Excel workbook with no sheet chosen, or a non-scalar JSON field value all raise `IngestFormatError` with a specific, actionable message — never a best-effort auto-flattening.
- All 198 existing tests must keep passing unmodified after every task (the delimited-text path is not touched).
- Design spec: `docs/superpowers/specs/2026-08-26-json-excel-ingest-design.md` — refer back to it if a task's intent is unclear.

---

### Task 1: `ingest.py` — format detection, error type, and cell stringification

**Files:**
- Modify: `src/dataforensics/ingest.py` (add imports + three new top-level pieces; nothing existing is changed)
- Test: `tests/unit/test_ingest_json_excel.py` (new file)

**Interfaces:**
- Produces: `detect_file_format(path: Path) -> str` (returns `"delimited"`, `"json"`, or `"excel"`); `class IngestFormatError(Exception)`; `_stringify_cell(value) -> str` (module-private, used internally by Tasks 2 and 3)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_ingest_json_excel.py`:

```python
import datetime

from dataforensics.ingest import IngestFormatError, _stringify_cell, detect_file_format


def test_detect_file_format_csv():
    assert detect_file_format_from_name("sample.csv") == "delimited"


def test_detect_file_format_tsv():
    assert detect_file_format_from_name("sample.tsv") == "delimited"


def test_detect_file_format_unrecognized_extension_is_delimited():
    assert detect_file_format_from_name("sample.txt") == "delimited"


def test_detect_file_format_json():
    assert detect_file_format_from_name("sample.json") == "json"


def test_detect_file_format_xlsx():
    assert detect_file_format_from_name("sample.xlsx") == "excel"


def test_detect_file_format_xls():
    assert detect_file_format_from_name("sample.xls") == "excel"


def test_detect_file_format_case_insensitive():
    assert detect_file_format_from_name("SAMPLE.JSON") == "json"
    assert detect_file_format_from_name("SAMPLE.XLSX") == "excel"


def detect_file_format_from_name(name: str) -> str:
    from pathlib import Path

    return detect_file_format(Path(name))


def test_ingest_format_error_is_an_exception():
    assert issubclass(IngestFormatError, Exception)


def test_stringify_cell_none_is_empty_string():
    assert _stringify_cell(None) == ""


def test_stringify_cell_bool_is_lowercase():
    assert _stringify_cell(True) == "true"
    assert _stringify_cell(False) == "false"


def test_stringify_cell_int():
    assert _stringify_cell(5) == "5"


def test_stringify_cell_float_non_whole():
    assert _stringify_cell(5.5) == "5.5"


def test_stringify_cell_float_whole_number_has_no_trailing_point_zero():
    # A spreadsheet cell holding "34" should stringify as "34", not "34.0" --
    # both openpyxl and xlrd can hand back a whole number as a float
    # depending on cell formatting, and the two backends must agree.
    assert _stringify_cell(34.0) == "34"


def test_stringify_cell_date():
    assert _stringify_cell(datetime.date(2024, 1, 15)) == "2024-01-15"


def test_stringify_cell_datetime():
    assert _stringify_cell(datetime.datetime(2024, 1, 15, 0, 0, 0)) == "2024-01-15T00:00:00"


def test_stringify_cell_string_passthrough():
    assert _stringify_cell("hello") == "hello"


def test_stringify_cell_bool_checked_before_int():
    # bool is a subclass of int in Python -- True must not stringify as "1".
    assert _stringify_cell(True) != "1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ingest_json_excel.py -v`
Expected: FAIL with `ImportError: cannot import name 'IngestFormatError'` (or similar — none of these names exist yet)

- [ ] **Step 3: Implement in `ingest.py`**

Add `import datetime` as the second line of `src/dataforensics/ingest.py` (after `from pathlib import Path`, so the top of the file reads):

```python
from pathlib import Path
import datetime

from charset_normalizer import from_path
```

Then append at the very end of the file (after `strip_footer`):

```python
class IngestFormatError(Exception):
    """Raised when a JSON or Excel input file doesn't match the shape
    DataForensics requires to safely treat it as tabular data -- e.g. JSON
    that isn't an array of flat objects, a JSON field whose value is itself
    an object/array, or a multi-sheet Excel workbook with no sheet chosen.
    Like DuplicateHeaderError, this signals a malformed-input-file
    condition, not a config problem -- callers should map it to exit code 3,
    not exit code 2."""


def detect_file_format(path: Path) -> str:
    """Returns "json", "excel", or "delimited" (the existing CSV/TSV path)
    based on the file extension alone -- no content sniffing. Any
    extension other than .json/.xlsx/.xls (including no extension, or an
    unrecognized one like .txt) is treated as delimited text, preserving
    today's behavior for every file this tool already accepts."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in (".xlsx", ".xls"):
        return "excel"
    return "delimited"


def _stringify_cell(value) -> str:
    """Converts one JSON/Excel scalar value to the plain-text form the rest
    of the engine expects, matching what the same value would look like if
    it had come from a CSV cell instead. Used by both read_json_rows and
    read_excel_rows so the two formats produce identical text for
    equivalent values.

    bool is checked before int/float because bool is a subclass of int in
    Python -- without this order, True would stringify as "1" instead of
    "true". datetime.datetime is checked before datetime.date for the same
    subclass reason (datetime.datetime IS-A datetime.date).
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    return str(value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ingest_json_excel.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: `199 passed` (198 existing + the new test file's 14 tests minus overlap — exact count doesn't matter, just zero failures)

- [ ] **Step 6: Commit**

```bash
git add src/dataforensics/ingest.py tests/unit/test_ingest_json_excel.py
git commit -m "$(cat <<'EOF'
Add file-format detection, IngestFormatError, and cell stringification

Foundational pieces for JSON/Excel input support: detect_file_format()
routes a path to "json"/"excel"/"delimited" by extension, and
_stringify_cell() converts a JSON/Excel scalar to the same plain-text
form a CSV cell would already produce.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `ingest.py` — `read_json_rows`

**Files:**
- Modify: `src/dataforensics/ingest.py` (append one new function; nothing existing changes)
- Test: `tests/unit/test_ingest_json_excel.py` (append)

**Interfaces:**
- Consumes: `IngestFormatError`, `_stringify_cell` (Task 1); `check_header_has_no_duplicates` (existing, same module)
- Produces: `read_json_rows(path: Path) -> list[dict[str, str]]`, consumed by Task 4 (`dictionary.py`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ingest_json_excel.py`:

```python
import json

import pytest

from dataforensics.ingest import read_json_rows


def test_read_json_rows_happy_path(tmp_path):
    f = tmp_path / "sample.json"
    f.write_text(json.dumps([
        {"participant_id": "001", "age": 34, "sex": "M"},
        {"participant_id": "002", "age": 29, "sex": "F"},
    ]))
    rows = read_json_rows(f)
    assert rows == [
        {"participant_id": "001", "age": "34", "sex": "M"},
        {"participant_id": "002", "age": "29", "sex": "F"},
    ]


def test_read_json_rows_empty_array_returns_empty_list(tmp_path):
    f = tmp_path / "empty.json"
    f.write_text("[]")
    assert read_json_rows(f) == []


def test_read_json_rows_ragged_keys_fill_missing_with_empty_string(tmp_path):
    f = tmp_path / "ragged.json"
    f.write_text(json.dumps([
        {"a": "1", "b": "2"},
        {"a": "3"},
    ]))
    rows = read_json_rows(f)
    assert rows == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": ""},
    ]


def test_read_json_rows_null_value_becomes_empty_string(tmp_path):
    f = tmp_path / "nulls.json"
    f.write_text(json.dumps([{"a": "1", "b": None}]))
    assert read_json_rows(f) == [{"a": "1", "b": ""}]


def test_read_json_rows_preserves_first_seen_key_order(tmp_path):
    f = tmp_path / "order.json"
    f.write_text(json.dumps([
        {"z": "1", "a": "2"},
        {"b": "3"},
    ]))
    rows = read_json_rows(f)
    assert list(rows[0].keys()) == ["z", "a", "b"]


def test_read_json_rows_invalid_json_raises_ingest_format_error(tmp_path):
    f = tmp_path / "broken.json"
    f.write_text("{not valid json")
    with pytest.raises(IngestFormatError):
        read_json_rows(f)


def test_read_json_rows_non_array_top_level_raises(tmp_path):
    f = tmp_path / "object.json"
    f.write_text(json.dumps({"a": "1"}))
    with pytest.raises(IngestFormatError, match="array"):
        read_json_rows(f)


def test_read_json_rows_non_object_element_raises(tmp_path):
    f = tmp_path / "scalars.json"
    f.write_text(json.dumps(["a", "b"]))
    with pytest.raises(IngestFormatError, match="element 0"):
        read_json_rows(f)


def test_read_json_rows_nested_array_value_raises(tmp_path):
    f = tmp_path / "nested.json"
    f.write_text(json.dumps([{"a": "1", "tags": ["x", "y"]}]))
    with pytest.raises(IngestFormatError, match="'tags'"):
        read_json_rows(f)


def test_read_json_rows_nested_object_value_raises(tmp_path):
    f = tmp_path / "nested_obj.json"
    f.write_text(json.dumps([{"a": "1", "detail": {"x": 1}}]))
    with pytest.raises(IngestFormatError, match="'detail'"):
        read_json_rows(f)
```

Add `IngestFormatError` and `json` to the existing top-of-file imports in `tests/unit/test_ingest_json_excel.py` (the file already imports `IngestFormatError` from Task 1 — just add `import json` and `import pytest` near the top of the file instead of inline if you prefer; either placement works, the tests above show them imported at point of use, which is also fine as written).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ingest_json_excel.py -v`
Expected: FAIL with `ImportError: cannot import name 'read_json_rows'`

- [ ] **Step 3: Implement in `ingest.py`**

Add `import json` near the top of `src/dataforensics/ingest.py` (alongside the `import datetime` from Task 1):

```python
from pathlib import Path
import datetime
import json

from charset_normalizer import from_path
```

Append at the end of the file (after `_stringify_cell`):

```python
def read_json_rows(path: Path) -> list[dict[str, str]]:
    """Reads a JSON file that must be a top-level array of flat objects
    (e.g. [{"age": 34, "sex": "F"}, ...]) into the same list[dict[str, str]]
    shape read_rows() produces for CSV/TSV. Never guesses at any other
    shape (a bare object, NDJSON, a nested array-under-a-key) -- see
    docs/superpowers/specs/2026-08-26-json-excel-ingest-design.md for why.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestFormatError(f"{path.name} is not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise IngestFormatError(
            f"{path.name}: expected a JSON array of objects (e.g. [{{...}}, {{...}}]), "
            f"got a top-level {type(data).__name__}"
        )

    if not data:
        return []

    header: list[str] = []
    seen_keys: set[str] = set()
    for i, element in enumerate(data):
        if not isinstance(element, dict):
            raise IngestFormatError(
                f"{path.name}: element {i} is not a JSON object (got {type(element).__name__}) "
                "-- every array element must be a flat object of column name -> value"
            )
        for key in element:
            if key not in seen_keys:
                seen_keys.add(key)
                header.append(key)

    rows: list[dict[str, str]] = []
    for i, element in enumerate(data):
        row: dict[str, str] = {}
        for key in header:
            value = element.get(key)
            if isinstance(value, (dict, list)):
                raise IngestFormatError(
                    f"{path.name}: column '{key}' at element {i} is a "
                    f"{type(value).__name__}, not a single value -- DataForensics "
                    "doesn't guess how to flatten nested JSON"
                )
            row[key] = _stringify_cell(value)
        rows.append(row)
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ingest_json_excel.py -v`
Expected: PASS (24 tests total in the file so far)

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, zero failures

- [ ] **Step 6: Commit**

```bash
git add src/dataforensics/ingest.py tests/unit/test_ingest_json_excel.py
git commit -m "$(cat <<'EOF'
Add read_json_rows for array-of-flat-objects JSON input

Requires a top-level JSON array of flat objects, matching the
"never guess" pattern already used for duplicate headers and
ambiguous dates -- a non-array top level, a non-object element, or a
nested object/array field value all raise IngestFormatError with a
specific message rather than attempting an implicit flatten.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `ingest.py` — `read_excel_rows` and `list_excel_sheets`

**Files:**
- Modify: `pyproject.toml` (add `openpyxl`, `xlrd` to core `dependencies`; add `xlwt` to the `dev` extra, test-fixture-generation only)
- Modify: `src/dataforensics/ingest.py` (append new functions; nothing existing changes)
- Test: `tests/unit/test_ingest_json_excel.py` (append)

**Interfaces:**
- Consumes: `IngestFormatError`, `_stringify_cell` (Task 1); `check_header_has_no_duplicates` (existing)
- Produces: `read_excel_rows(path: Path, sheet: str | None = None) -> list[dict[str, str]]` and `list_excel_sheets(path: Path) -> list[str]`, both consumed by Task 4 (`dictionary.py`), Task 5 (`cli.py`), and Task 6 (`app.py`)

- [ ] **Step 1: Add dependencies and install**

Edit `pyproject.toml`'s `dependencies` list to add the two runtime Excel libraries:

```toml
dependencies = [
    "polars>=1.0",
    "click>=8.1",
    "pyyaml>=6.0",
    "charset-normalizer>=3.3",
    "rapidfuzz>=3.9",
    "openpyxl>=3.1",
    "xlrd>=2.0",
]
```

Edit the `dev` extra to add `xlwt` (used only by this task's tests, to generate a real legacy `.xls` fixture file — `openpyxl` cannot write `.xls`, only `.xlsx`):

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "xlwt>=1.3"]
viewer = ["streamlit>=1.30"]
```

Run: `.venv/bin/pip install -e ".[dev,viewer]"`
Expected: installs `openpyxl`, `xlrd`, `xlwt` with no errors (verify with `.venv/bin/python -c "import openpyxl, xlrd, xlwt; print('ok')"`, expect `ok`)

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_ingest_json_excel.py`:

```python
import datetime as dt

import openpyxl
import xlwt

from dataforensics.ingest import list_excel_sheets, read_excel_rows


def _write_xlsx(path, rows, sheet_name="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_read_excel_rows_xlsx_happy_path(tmp_path):
    f = tmp_path / "sample.xlsx"
    _write_xlsx(f, [
        ["participant_id", "age", "sex"],
        ["001", 34, "M"],
        ["002", 29, "F"],
    ])
    rows = read_excel_rows(f)
    assert rows == [
        {"participant_id": "001", "age": "34", "sex": "M"},
        {"participant_id": "002", "age": "29", "sex": "F"},
    ]


def test_read_excel_rows_xlsx_stringifies_dates_and_booleans(tmp_path):
    f = tmp_path / "types.xlsx"
    _write_xlsx(f, [
        ["id", "visit_date", "consented"],
        [1, dt.date(2024, 1, 15), True],
    ])
    rows = read_excel_rows(f)
    assert rows == [{"id": "1", "visit_date": "2024-01-15", "consented": "true"}]


def test_read_excel_rows_xlsx_empty_sheet_returns_empty_list(tmp_path):
    f = tmp_path / "empty.xlsx"
    _write_xlsx(f, [])
    assert read_excel_rows(f) == []


def test_read_excel_rows_xlsx_skips_fully_blank_trailing_rows(tmp_path):
    f = tmp_path / "blank_row.xlsx"
    _write_xlsx(f, [
        ["a", "b"],
        ["1", "2"],
        [None, None],
    ])
    assert read_excel_rows(f) == [{"a": "1", "b": "2"}]


def test_read_excel_rows_xlsx_duplicate_header_raises_duplicate_header_error(tmp_path):
    from dataforensics.ingest import DuplicateHeaderError

    f = tmp_path / "dupe.xlsx"
    _write_xlsx(f, [["a", "a"], ["1", "2"]])
    with pytest.raises(DuplicateHeaderError):
        read_excel_rows(f)


def test_read_excel_rows_xlsx_multi_sheet_without_choice_raises(tmp_path):
    f = tmp_path / "multi.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "First"
    ws1.append(["a"])
    ws1.append(["1"])
    ws2 = wb.create_sheet("Second")
    ws2.append(["b"])
    ws2.append(["2"])
    wb.save(f)

    with pytest.raises(IngestFormatError, match="First.*Second|Second.*First"):
        read_excel_rows(f)


def test_read_excel_rows_xlsx_multi_sheet_with_explicit_choice_succeeds(tmp_path):
    f = tmp_path / "multi2.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "First"
    ws1.append(["a"])
    ws1.append(["1"])
    ws2 = wb.create_sheet("Second")
    ws2.append(["b"])
    ws2.append(["2"])
    wb.save(f)

    assert read_excel_rows(f, sheet="Second") == [{"b": "2"}]


def test_read_excel_rows_xlsx_unknown_sheet_name_raises(tmp_path):
    f = tmp_path / "single.xlsx"
    _write_xlsx(f, [["a"], ["1"]])
    with pytest.raises(IngestFormatError, match="no sheet named"):
        read_excel_rows(f, sheet="DoesNotExist")


def test_list_excel_sheets_single_sheet(tmp_path):
    f = tmp_path / "single.xlsx"
    _write_xlsx(f, [["a"], ["1"]], sheet_name="OnlySheet")
    assert list_excel_sheets(f) == ["OnlySheet"]


def test_read_excel_rows_xls_happy_path(tmp_path):
    f = tmp_path / "sample.xls"
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    for r, row in enumerate([["participant_id", "age"], ["001", 34], ["002", 29]]):
        for c, value in enumerate(row):
            ws.write(r, c, value)
    wb.save(str(f))

    rows = read_excel_rows(f)
    assert rows == [
        {"participant_id": "001", "age": "34"},
        {"participant_id": "002", "age": "29"},
    ]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_ingest_json_excel.py -v -k excel`
Expected: FAIL with `ImportError: cannot import name 'read_excel_rows'`

- [ ] **Step 4: Implement in `ingest.py`**

Append at the end of the file (after `read_json_rows`):

```python
def _xlsx_backend(path: Path):
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet_names = wb.sheetnames

    def get_grid(sheet_name: str) -> list[tuple]:
        return [row for row in wb[sheet_name].iter_rows(values_only=True)]

    return sheet_names, get_grid


def _xls_cell_value(cell, datemode):
    import xlrd

    if cell.ctype == xlrd.XL_CELL_EMPTY:
        return None
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(cell.value, datemode)
    return cell.value


def _xls_backend(path: Path):
    import xlrd

    book = xlrd.open_workbook(str(path))
    sheet_names = book.sheet_names()

    def get_grid(sheet_name: str) -> list[list]:
        ws = book.sheet_by_name(sheet_name)
        return [
            [_xls_cell_value(ws.cell(r, c), book.datemode) for c in range(ws.ncols)]
            for r in range(ws.nrows)
        ]

    return sheet_names, get_grid


def _excel_backend(path: Path):
    if path.suffix.lower() == ".xlsx":
        return _xlsx_backend(path)
    return _xls_backend(path)


def list_excel_sheets(path: Path) -> list[str]:
    """Lists the sheet names in an Excel workbook, in workbook order.
    Exists mainly for an interactive caller (e.g. the Streamlit app) that
    needs to show a sheet picker before calling read_excel_rows."""
    sheet_names, _get_grid = _excel_backend(path)
    return sheet_names


def read_excel_rows(path: Path, sheet: str | None = None) -> list[dict[str, str]]:
    """Reads one sheet of an .xlsx or .xls workbook into the same
    list[dict[str, str]] shape read_rows() produces for CSV/TSV. If the
    workbook has more than one sheet and `sheet` is not given, raises
    IngestFormatError listing the sheet names -- never silently picks one.
    A fully-blank row (every cell None) is skipped rather than becoming an
    all-empty-string row, since Excel commonly reports trailing blank rows
    that were never really part of the dataset.
    """
    sheet_names, get_grid = _excel_backend(path)

    if sheet is None:
        if len(sheet_names) > 1:
            raise IngestFormatError(
                f"{path.name} has multiple sheets ({', '.join(sheet_names)}) -- "
                "pass --sheet to choose one"
            )
        chosen = sheet_names[0]
    else:
        if sheet not in sheet_names:
            raise IngestFormatError(
                f"{path.name} has no sheet named '{sheet}' -- available sheets: "
                f"{', '.join(sheet_names)}"
            )
        chosen = sheet

    grid = get_grid(chosen)
    if not grid:
        return []

    header = [_stringify_cell(v) for v in grid[0]]
    check_header_has_no_duplicates(header)

    rows: list[dict[str, str]] = []
    for raw_row in grid[1:]:
        if all(v is None for v in raw_row):
            continue
        row = {
            name: _stringify_cell(v)
            for name, v in zip(header, list(raw_row) + [None] * (len(header) - len(raw_row)))
        }
        rows.append(row)
    return rows
```

Note on the padding line inside the loop above: `list(raw_row) + [None] * (len(header) - len(raw_row))` pads `raw_row` with `None`s up to `len(header)` when the row is shorter (an Excel row where trailing cells were never written). If `raw_row` is longer than `header`, the multiplier is negative so `[None] * negative` evaluates to `[]` (no padding added) and `zip` stops at the shorter of the two — matching the same "extra fields beyond the header are dropped" behavior the CSV path already has via `zip_longest(header, row, fillvalue="")` in `dictionary.build_data_dictionary`. No new import is needed for this — plain `zip` plus the list-padding expression is sufficient, so `itertools.zip_longest` is not added to `ingest.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_ingest_json_excel.py -v`
Expected: PASS (all tests in the file, ~35 total)

- [ ] **Step 6: Run the full existing suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, zero failures

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/dataforensics/ingest.py tests/unit/test_ingest_json_excel.py
git commit -m "$(cat <<'EOF'
Add read_excel_rows and list_excel_sheets for .xlsx/.xls input

openpyxl backs .xlsx, xlrd backs legacy .xls; both funnel through the
same header/row/multi-sheet logic and _stringify_cell so the two
formats behave identically. A workbook with more than one sheet and
no explicit `sheet` argument raises IngestFormatError listing the
sheet names, matching the project's "never guess" pattern.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `dictionary.py` — route `read_rows`/`build_data_dictionary` through the new formats

**Files:**
- Modify: `src/dataforensics/dictionary.py:1-5, 39-45, 137-143` (add imports, add `_load_table`, change the two public functions' bodies — their existing signatures gain an optional `sheet` parameter, and their aggregation logic is otherwise byte-for-byte unchanged)
- Test: `tests/unit/test_dictionary.py` (append)

**Interfaces:**
- Consumes: `detect_file_format`, `read_json_rows`, `read_excel_rows` (Tasks 1-3)
- Produces: `read_rows(path: Path, sheet: str | None = None) -> list[dict]` and `build_data_dictionary(path: Path, include_raw_samples: bool = False, sheet: str | None = None) -> dict` — both signatures gain `sheet`, existing callers that don't pass it are unaffected. Consumed by Task 5 (`cli.py`) and Task 6 (`app.py`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dictionary.py`:

```python
import json


def test_build_data_dictionary_from_json(tmp_path):
    f = tmp_path / "sample.json"
    f.write_text(json.dumps([
        {"participant_id": "001", "sex": "M", "age": 34, "notes": "fine"},
        {"participant_id": "002", "sex": "F", "age": 29, "notes": "fine"},
        {"participant_id": "003", "sex": "M", "age": None, "notes": "fine"},
        {"participant_id": "004", "sex": "F", "age": 41, "notes": "fine"},
    ]))
    d = build_data_dictionary(f)

    assert d["participant_id"]["category"] == "id"
    assert d["age"]["null_count"] == 1
    assert d["age"]["non_null_pct"] == 75.0
    assert d["sex"]["category"] == "categorical"
    assert set(d["sex"]["levels"]) == {"M", "F"}


def test_read_rows_from_json(tmp_path):
    f = tmp_path / "sample.json"
    f.write_text(json.dumps([{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]))
    assert read_rows(f) == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_build_data_dictionary_from_excel(tmp_path):
    import openpyxl

    f = tmp_path / "sample.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["participant_id", "sex", "age"])
    ws.append(["001", "M", 34])
    ws.append(["002", "F", 29])
    ws.append(["003", "M", None])
    ws.append(["004", "F", 41])
    wb.save(f)

    d = build_data_dictionary(f)
    assert d["participant_id"]["category"] == "id"
    assert d["age"]["null_count"] == 1
    assert d["sex"]["category"] == "categorical"


def test_read_rows_from_excel_with_explicit_sheet(tmp_path):
    import openpyxl

    f = tmp_path / "multi.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "First"
    ws1.append(["a"])
    ws1.append(["1"])
    ws2 = wb.create_sheet("Second")
    ws2.append(["b"])
    ws2.append(["2"])
    wb.save(f)

    assert read_rows(f, sheet="Second") == [{"b": "2"}]


def test_build_data_dictionary_empty_json_array_returns_empty_dict(tmp_path):
    f = tmp_path / "empty.json"
    f.write_text("[]")
    assert build_data_dictionary(f) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_dictionary.py -v -k "json or excel"`
Expected: FAIL — `build_data_dictionary`/`read_rows` currently try to parse the `.json`/`.xlsx` file as delimited text and either raise or produce wrong results (not a clean `ImportError` this time, since the functions already exist — the failures will be assertion mismatches or a `UnicodeDecodeError` from trying to `.read_text()` a binary `.xlsx` file).

- [ ] **Step 3: Implement in `dictionary.py`**

Change the imports at the top of `src/dataforensics/dictionary.py` from:

```python
from dataforensics.ingest import check_header_has_no_duplicates, detect_delimiter, detect_encoding, strip_footer
```

to:

```python
from dataforensics.ingest import (
    check_header_has_no_duplicates,
    detect_delimiter,
    detect_encoding,
    detect_file_format,
    read_excel_rows,
    read_json_rows,
    strip_footer,
)
```

Add this new private function right after `_read_cleaned_lines` (before `_cardinality_cap`):

```python
def _load_table(path: Path, sheet: str | None = None) -> tuple[list[str], list[list[str]]]:
    """Returns (header, body_rows) for any supported input format. The
    delimited-text branch is exactly what build_data_dictionary and
    read_rows already did inline before this function existed -- moved
    here unchanged so both functions share one implementation instead of
    two copies that could drift apart. The json/excel branches convert
    read_json_rows'/read_excel_rows' list[dict[str, str]] into the same
    (header, body_rows) shape, since every row from those readers has
    identical keys in the same order by construction.
    """
    fmt = detect_file_format(path)
    if fmt == "delimited":
        data_lines, delimiter = _read_cleaned_lines(path)
        if not data_lines:
            return [], []
        header = data_lines[0].split(delimiter)
        check_header_has_no_duplicates(header)
        body_rows = [line.split(delimiter) for line in data_lines[1:]]
        return header, body_rows

    rows = read_json_rows(path) if fmt == "json" else read_excel_rows(path, sheet=sheet)
    if not rows:
        return [], []
    header = list(rows[0].keys())
    body_rows = [[row[name] for name in header] for row in rows]
    return header, body_rows
```

Replace the first two lines of `build_data_dictionary` (currently):

```python
def build_data_dictionary(path: Path, include_raw_samples: bool = False) -> dict:
    data_lines, delimiter = _read_cleaned_lines(path)
    if not data_lines:
        return {}
    header = data_lines[0].split(delimiter)
    check_header_has_no_duplicates(header)
    body_rows = [line.split(delimiter) for line in data_lines[1:]]
    n_rows = len(body_rows)
```

with:

```python
def build_data_dictionary(path: Path, include_raw_samples: bool = False, sheet: str | None = None) -> dict:
    header, body_rows = _load_table(path, sheet=sheet)
    if not header:
        return {}
    n_rows = len(body_rows)
```

Everything below that line in `build_data_dictionary` (the `columns: dict[str, list[str]] = {name: [] for name in header}` loop through the end of the function) stays exactly as it is — do not change it.

Replace `read_rows` (currently):

```python
def read_rows(path: Path) -> list[dict]:
    data_lines, delimiter = _read_cleaned_lines(path)
    if not data_lines:
        return []
    header = data_lines[0].split(delimiter)
    check_header_has_no_duplicates(header)
    return [dict(zip_longest(header, line.split(delimiter), fillvalue="")) for line in data_lines[1:]]
```

with:

```python
def read_rows(path: Path, sheet: str | None = None) -> list[dict]:
    header, body_rows = _load_table(path, sheet=sheet)
    if not header:
        return []
    return [dict(zip_longest(header, row, fillvalue="")) for row in body_rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_dictionary.py -v`
Expected: PASS (all tests in the file, including every pre-existing one)

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, zero failures — this is the step that proves the delimited-text refactor is truly behavior-preserving

- [ ] **Step 6: Commit**

```bash
git add src/dataforensics/dictionary.py tests/unit/test_dictionary.py
git commit -m "$(cat <<'EOF'
Route read_rows/build_data_dictionary through JSON/Excel readers

Both functions now derive (header, body_rows) via a shared _load_table
helper that dispatches on detect_file_format() -- the delimited-text
branch is the exact same code that was previously duplicated inline in
both functions, just factored into one place. Both gain an optional
`sheet` parameter for multi-sheet Excel input.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `cli.py` — format-aware safety-net anchor, `--sheet` option, exit-code mapping

**Files:**
- Modify: `src/dataforensics/cli.py` (imports at top; `_read_header_and_row_count` at lines 33-87; `scan` at lines 126-185; `harmonize` at lines 251-274; `_harmonize_single_file` at lines 277-365; `_harmonize_crosswalk`'s two `except DuplicateHeaderError` sites at lines ~478 and ~500)
- Test: `tests/integration/test_scan_command.py`, `tests/integration/test_harmonize_dry_run.py` (append)

**Interfaces:**
- Consumes: `IngestFormatError`, `detect_file_format`, `read_json_rows`, `read_excel_rows` (Tasks 1-3); `read_rows`, `build_data_dictionary` with their new `sheet` parameter (Task 4)
- Produces: `scan --sheet NAME` and `harmonize --sheet NAME` (single-file mode only) CLI options

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_scan_command.py`:

```python
import openpyxl


def test_scan_accepts_json_input(tmp_path):
    src = tmp_path / "sample.json"
    src.write_text(json.dumps([{"id": "001", "age": 34}, {"id": "002", "age": 29}]))

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path)])

    assert result.exit_code == 0
    payload = json.loads((tmp_path / "sample.data_dictionary.json").read_text())
    assert "age" in payload


def test_scan_malformed_json_exits_3(tmp_path):
    src = tmp_path / "broken.json"
    src.write_text("{not valid")

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path)])

    assert result.exit_code == 3
    assert "Malformed input file" in result.output


def test_scan_accepts_xlsx_input(tmp_path):
    src = tmp_path / "sample.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "age"])
    ws.append(["001", 34])
    ws.append(["002", 29])
    wb.save(src)

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path)])

    assert result.exit_code == 0
    payload = json.loads((tmp_path / "sample.data_dictionary.json").read_text())
    assert "age" in payload


def test_scan_xlsx_multi_sheet_requires_sheet_option(tmp_path):
    src = tmp_path / "multi.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "First"
    ws1.append(["a"])
    ws1.append(["1"])
    ws2 = wb.create_sheet("Second")
    ws2.append(["b"])
    ws2.append(["2"])
    wb.save(src)

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path)])
    assert result.exit_code == 3
    assert "multiple sheets" in result.output

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path), "--sheet", "Second"])
    assert result.exit_code == 0
    payload = json.loads((tmp_path / "multi.data_dictionary.json").read_text())
    assert "b" in payload
```

Append to `tests/integration/test_harmonize_dry_run.py`:

```python
def test_harmonize_dry_run_accepts_json_input(tmp_path):
    src = tmp_path / "sample.json"
    src.write_text('[{"participant_id": "1", "smoking_status": "99"}, {"participant_id": "2", "smoking_status": "10"}]')
    rules_path = _write_rules(tmp_path)
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert not output_path.exists()
    assert "smoking_status" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_scan_command.py tests/integration/test_harmonize_dry_run.py -v -k "json or xlsx"`
Expected: FAIL — `scan`/`harmonize` currently try to parse these files as delimited text (`UnicodeDecodeError` for the `.xlsx` cases, garbage/empty parsing for `.json`), and `--sheet` is not a recognized option yet (`Error: No such option: --sheet`)

- [ ] **Step 3: Implement in `cli.py`**

Change the import block near the top of `cli.py` from:

```python
from dataforensics.ingest import DuplicateHeaderError, check_header_has_no_duplicates, detect_delimiter, detect_encoding, strip_footer
```

to:

```python
from dataforensics.ingest import (
    DuplicateHeaderError,
    IngestFormatError,
    check_header_has_no_duplicates,
    detect_delimiter,
    detect_encoding,
    detect_file_format,
    read_excel_rows,
    read_json_rows,
    strip_footer,
)
```

Replace `_read_header_and_row_count`'s signature and body (keep its existing docstring exactly as-is, just append one sentence to it, and change the code below it):

Change the signature line from:
```python
def _read_header_and_row_count(path: Path) -> tuple[list[str], int, int]:
```
to:
```python
def _read_header_and_row_count(path: Path, sheet: str | None = None) -> tuple[list[str], int, int]:
```

At the end of the existing docstring (after the paragraph ending "...would NOT be caught by the safety net this anchor feeds."), add one more paragraph:

```
    For JSON/Excel input this same independence is preserved by calling
    ingest.read_json_rows/read_excel_rows directly here too, rather than
    going through dictionary.py's _load_table -- the same "two bindings of
    one shared primitive, not two independent implementations" bound as
    the delimited-text case above.
```

Replace the function body (currently, right after the docstring's closing `"""`):

```python
    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, stripped = strip_footer(raw_lines, delimiter)
    if not data_lines:
        return [], 0, len(stripped)
    header = data_lines[0].split(delimiter)
    check_header_has_no_duplicates(header)
    return header, len(data_lines) - 1, len(stripped)
```

with:

```python
    fmt = detect_file_format(path)
    if fmt != "delimited":
        rows = read_json_rows(path) if fmt == "json" else read_excel_rows(path, sheet=sheet)
        header = list(rows[0].keys()) if rows else []
        return header, len(rows), 0

    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, stripped = strip_footer(raw_lines, delimiter)
    if not data_lines:
        return [], 0, len(stripped)
    header = data_lines[0].split(delimiter)
    check_header_has_no_duplicates(header)
    return header, len(data_lines) - 1, len(stripped)
```

Update the `scan` command. Change:

```python
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

    try:
        dictionary = build_data_dictionary(file_path)
    except DuplicateHeaderError as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)

    try:
        _stripped_header, stripped_row_count, stripped_count = _read_header_and_row_count(file_path)
    except DuplicateHeaderError:
        stripped_row_count, stripped_count = 0, 0
    _warn_if_footer_stripped(file_path, stripped_count, stripped_row_count)
```

to:

```python
@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--out-dir", type=click.Path(), default=".")
@click.option("--sheet", "sheet", default=None, help="Sheet name for a multi-sheet Excel input (ignored for non-Excel input).")
def scan(file, rules_path, out_dir, sheet):
    """Read-only: emit a data dictionary and, if --rules is given, a validation report."""
    file_path = Path(file)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    stem = file_path.stem

    try:
        dictionary = build_data_dictionary(file_path, sheet=sheet)
    except (DuplicateHeaderError, IngestFormatError) as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)

    try:
        _stripped_header, stripped_row_count, stripped_count = _read_header_and_row_count(file_path, sheet=sheet)
    except (DuplicateHeaderError, IngestFormatError):
        stripped_row_count, stripped_count = 0, 0
    _warn_if_footer_stripped(file_path, stripped_count, stripped_row_count)
```

Further down in `scan`, change:

```python
    try:
        rows = read_rows(file_path)
    except DuplicateHeaderError as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)
```

to:

```python
    try:
        rows = read_rows(file_path, sheet=sheet)
    except (DuplicateHeaderError, IngestFormatError) as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)
```

Update the `harmonize` command's signature and dispatch. Change:

```python
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
```

to:

```python
@main.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True), required=True)
@click.option("--rules", "rules_path", type=click.Path(exists=True), default=None)
@click.option("--output", type=click.Path(), default=None)
@click.option("--rules-map", default=None)
@click.option("--crosswalk", "crosswalk_path", type=click.Path(exists=True), default=None)
@click.option("--output-dir", type=click.Path(), default=None)
@click.option("--execute", is_flag=True, default=False)
@click.option("--sheet", "sheet", default=None, help="Sheet name for a multi-sheet Excel input (single-file mode only; ignored otherwise).")
def harmonize(files, rules_path, output, rules_map, crosswalk_path, output_dir, execute, sheet):
    """Rules-driven, dry-run-by-default transform. Single file or cross-dataset crosswalk."""
    if len(files) == 1 and rules_path is not None:
        _harmonize_single_file(files[0], rules_path, output, execute, sheet)
        return
```

Update `_harmonize_single_file`'s signature and its two `read_rows`/`_read_header_and_row_count` call sites. Change:

```python
def _harmonize_single_file(file, rules_path, output, execute):
    file_path = Path(file)
```

to:

```python
def _harmonize_single_file(file, rules_path, output, execute, sheet):
    file_path = Path(file)
```

Change:

```python
    try:
        rows = read_rows(file_path)
    except DuplicateHeaderError as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)
    plan = plan_transformations(rows, rules)
```

to:

```python
    try:
        rows = read_rows(file_path, sheet=sheet)
    except (DuplicateHeaderError, IngestFormatError) as exc:
        click.echo(f"Malformed input file {file_path}: {exc}", err=True)
        sys.exit(3)
    plan = plan_transformations(rows, rules)
```

Change:

```python
    anchor_header, anchor_row_count, anchor_stripped_count = _read_header_and_row_count(file_path)
```

to:

```python
    anchor_header, anchor_row_count, anchor_stripped_count = _read_header_and_row_count(file_path, sheet=sheet)
```

In `_harmonize_crosswalk` (no `--sheet` plumbing here — a multi-sheet Excel source in crosswalk mode raises a clear `IngestFormatError` telling the user to split/convert it, which is an accepted v1 limitation per the design spec's "Out of scope" section), widen both existing `except DuplicateHeaderError` clauses. Change:

```python
        try:
            (
                file_headers[str(file_path)],
                file_row_counts[str(file_path)],
                file_stripped_counts[str(file_path)],
            ) = _read_header_and_row_count(file_path)
        except DuplicateHeaderError as exc:
            click.echo(f"Malformed input file {file_path}: {exc}", err=True)
            sys.exit(3)
```

to:

```python
        try:
            (
                file_headers[str(file_path)],
                file_row_counts[str(file_path)],
                file_stripped_counts[str(file_path)],
            ) = _read_header_and_row_count(file_path)
        except (DuplicateHeaderError, IngestFormatError) as exc:
            click.echo(f"Malformed input file {file_path}: {exc}", err=True)
            sys.exit(3)
```

And change:

```python
        try:
            rows = read_rows(file_path)
        except DuplicateHeaderError as exc:
            # Defensive backstop only -- pass 1 already validated every
            # source's header via _read_header_and_row_count above.
            click.echo(f"Malformed input file {file_path}: {exc}", err=True)
            sys.exit(3)
```

to:

```python
        try:
            rows = read_rows(file_path)
        except (DuplicateHeaderError, IngestFormatError) as exc:
            # Defensive backstop only -- pass 1 already validated every
            # source's header via _read_header_and_row_count above.
            click.echo(f"Malformed input file {file_path}: {exc}", err=True)
            sys.exit(3)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_scan_command.py tests/integration/test_harmonize_dry_run.py -v`
Expected: PASS (all tests in both files)

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, zero failures

- [ ] **Step 6: Commit**

```bash
git add src/dataforensics/cli.py tests/integration/test_scan_command.py tests/integration/test_harmonize_dry_run.py
git commit -m "$(cat <<'EOF'
Wire JSON/Excel input through the CLI (scan, harmonize)

scan and harmonize now accept .json/.xlsx/.xls transparently, with a
new --sheet option for a multi-sheet Excel input (single-file
harmonize mode and scan only -- crosswalk mode surfaces a clear error
for an unresolved multi-sheet source instead). IngestFormatError is
now caught everywhere DuplicateHeaderError already was, mapping to the
same exit code 3.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `app.py` — upload widgets, sheet picker, and error handling

**Files:**
- Modify: `app.py` (imports; `_sniff_header` at lines 161-168; the Analyze & Clean tab's upload/dedup block at lines 189-248; the "ragged row" warning at lines 251-259; the Multi-File Relationships tab's upload/skip block at lines 596-615)

**Interfaces:**
- Consumes: `detect_file_format`, `IngestFormatError`, `list_excel_sheets`, `read_json_rows`, `read_excel_rows` (Tasks 1-3); `read_rows`/`build_data_dictionary` with `sheet` (Task 4)

No pytest coverage exists for `app.py` today (it's a Streamlit UI, verified live per this project's established convention — see README's "Interactive app" section). This task is verified by starting the app and driving it in a browser, not by automated tests.

- [ ] **Step 1: Update imports**

Find this line near the top of `app.py`:

```python
from dataforensics.ingest import DuplicateHeaderError, check_header_has_no_duplicates, deduplicate_header
```

Replace it with:

```python
from dataforensics.ingest import (
    DuplicateHeaderError,
    IngestFormatError,
    check_header_has_no_duplicates,
    deduplicate_header,
    detect_file_format,
    list_excel_sheets,
)
```

- [ ] **Step 2: Make `_sniff_header` format-aware**

Replace (lines 161-168):

```python
def _sniff_header(path: Path) -> list[str]:
    from dataforensics.ingest import detect_delimiter, detect_encoding, strip_footer

    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, _ = strip_footer(raw_lines, delimiter)
    return data_lines[0].split(delimiter) if data_lines else []
```

with:

```python
def _sniff_header(path: Path, sheet: str | None = None) -> list[str]:
    from dataforensics.ingest import detect_delimiter, detect_encoding, read_excel_rows, read_json_rows, strip_footer

    fmt = detect_file_format(path)
    if fmt == "json":
        rows = read_json_rows(path)
        return list(rows[0].keys()) if rows else []
    if fmt == "excel":
        rows = read_excel_rows(path, sheet=sheet)
        return list(rows[0].keys()) if rows else []

    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, _ = strip_footer(raw_lines, delimiter)
    return data_lines[0].split(delimiter) if data_lines else []
```

- [ ] **Step 3: Widen the Analyze & Clean tab's upload widget**

Change (line 195):

```python
        data_file = st.file_uploader("Upload a CSV/TSV file", type=["csv", "tsv"], label_visibility="visible")
```

to:

```python
        data_file = st.file_uploader(
            "Upload a CSV/TSV/JSON/Excel file", type=["csv", "tsv", "json", "xlsx", "xls"], label_visibility="visible"
        )
```

- [ ] **Step 4: Add the sheet picker and route duplicate-header/format-error handling by format**

Replace the whole block from `raw_path = _write_temp(...)` (line 214) through the end of the duplicate-header `try/except` (line 241) — currently:

```python
    raw_path = _write_temp(st.session_state["dataforensics_data_name"], st.session_state["dataforensics_data_bytes"])

    # --- Duplicate-header recovery: a real path forward, not a dead end ---
    try:
        check_header_has_no_duplicates(_sniff_header(raw_path))
        data_path = raw_path
    except DuplicateHeaderError as exc:
        _step_bar(1)
        st.markdown(
            f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-error">Blocking</span>'
            f'<div class="dataforensics-card-title">Duplicate column names found</div>'
            f'<div class="dataforensics-card-evidence">{_esc(exc)}</div></div>',
            unsafe_allow_html=True,
        )
        st.write(
            "This file can't be profiled safely as-is — with two columns sharing a name, "
            "one of them would silently lose its data. Choose how to proceed:"
        )
        c1, c2 = st.columns(2)
        if c1.button("Auto-rename duplicates and continue (e.g. name → name, name_2)", type="primary"):
            data_path = _rewrite_with_deduplicated_header(raw_path)
            st.session_state["dataforensics_dedup_choice_made"] = str(data_path)
            st.rerun()
        c2.write("...or fix the file yourself and re-upload it above.")
        if st.session_state.get("dataforensics_dedup_choice_made"):
            data_path = Path(st.session_state["dataforensics_dedup_choice_made"])
        else:
            st.stop()
```

with:

```python
    raw_path = _write_temp(st.session_state["dataforensics_data_name"], st.session_state["dataforensics_data_bytes"])
    raw_format = detect_file_format(raw_path)

    # --- Multi-sheet Excel: ask which sheet before doing anything else ---
    sheet_choice = None
    if raw_format == "excel":
        sheet_names = list_excel_sheets(raw_path)
        if len(sheet_names) > 1:
            sheet_choice = st.selectbox(
                "This workbook has multiple sheets — choose one to analyze",
                sheet_names,
                key="dataforensics_sheet_choice",
            )
        elif sheet_names:
            sheet_choice = sheet_names[0]

    # --- Duplicate-header / malformed-input recovery: a real path forward, not a dead end ---
    try:
        check_header_has_no_duplicates(_sniff_header(raw_path, sheet=sheet_choice))
        data_path = raw_path
    except DuplicateHeaderError as exc:
        _step_bar(1)
        st.markdown(
            f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-error">Blocking</span>'
            f'<div class="dataforensics-card-title">Duplicate column names found</div>'
            f'<div class="dataforensics-card-evidence">{_esc(exc)}</div></div>',
            unsafe_allow_html=True,
        )
        if raw_format == "delimited":
            st.write(
                "This file can't be profiled safely as-is — with two columns sharing a name, "
                "one of them would silently lose its data. Choose how to proceed:"
            )
            c1, c2 = st.columns(2)
            if c1.button("Auto-rename duplicates and continue (e.g. name → name, name_2)", type="primary"):
                data_path = _rewrite_with_deduplicated_header(raw_path)
                st.session_state["dataforensics_dedup_choice_made"] = str(data_path)
                st.rerun()
            c2.write("...or fix the file yourself and re-upload it above.")
            if st.session_state.get("dataforensics_dedup_choice_made"):
                data_path = Path(st.session_state["dataforensics_dedup_choice_made"])
            else:
                st.stop()
        else:
            st.write("Fix the duplicate column names in the source file and re-upload it above.")
            st.stop()
    except IngestFormatError as exc:
        _step_bar(1)
        st.markdown(
            f'<div class="dataforensics-card"><span class="dataforensics-badge dataforensics-badge-error">Blocking</span>'
            f'<div class="dataforensics-card-title">Can\'t read this file</div>'
            f'<div class="dataforensics-card-evidence">{_esc(exc)}</div></div>',
            unsafe_allow_html=True,
        )
        st.stop()
```

- [ ] **Step 5: Thread `sheet_choice` into Step 2's reads and guard the CSV-specific warning**

Change (lines 246-259):

```python
    _step_bar(2)
    dictionary = build_data_dictionary(data_path)
    rows = read_rows(data_path)
    columns = list(dictionary.keys())

    ragged_row_count = sum(1 for r in rows if "" in r)
    if ragged_row_count:
        st.warning(
            f"⚠ {ragged_row_count} row(s) have more fields than the header — usually an "
            "unescaped comma/delimiter inside a text value (this parser isn't CSV-quote-aware; "
            "see the README's Known Limitations). The overflow content is preserved under a "
            "column named \"\" rather than dropped, but you may want to fix the source file's "
            "quoting for a cleaner result."
        )
```

to:

```python
    _step_bar(2)
    dictionary = build_data_dictionary(data_path, sheet=sheet_choice)
    rows = read_rows(data_path, sheet=sheet_choice)
    columns = list(dictionary.keys())

    ragged_row_count = sum(1 for r in rows if "" in r)
    if ragged_row_count and raw_format == "delimited":
        st.warning(
            f"⚠ {ragged_row_count} row(s) have more fields than the header — usually an "
            "unescaped comma/delimiter inside a text value (this parser isn't CSV-quote-aware; "
            "see the README's Known Limitations). The overflow content is preserved under a "
            "column named \"\" rather than dropped, but you may want to fix the source file's "
            "quoting for a cleaner result."
        )
```

- [ ] **Step 6: Widen the Multi-File Relationships tab's upload widget and skip logic**

Change (lines 596-598):

```python
    multi_files = st.file_uploader(
        "Upload 2 or more CSV/TSV files", type=["csv", "tsv"], accept_multiple_files=True, key="multifile_uploader"
    )
```

to:

```python
    multi_files = st.file_uploader(
        "Upload 2 or more CSV/TSV/JSON/Excel files",
        type=["csv", "tsv", "json", "xlsx", "xls"],
        accept_multiple_files=True,
        key="multifile_uploader",
    )
```

Change (lines 603-615):

```python
        for f in multi_files:
            path = _write_temp(f.name, f.getvalue())
            try:
                check_header_has_no_duplicates(_sniff_header(path))
                file_rows[f.name] = read_rows(path)
            except DuplicateHeaderError:
                skipped.append(f.name)

        if skipped:
            st.warning(
                f"Skipped {', '.join(skipped)} — duplicate column names. "
                "Fix and re-upload, or use the Analyze & Clean tab's auto-rename option first."
            )
```

to:

```python
        for f in multi_files:
            path = _write_temp(f.name, f.getvalue())
            try:
                check_header_has_no_duplicates(_sniff_header(path))
                file_rows[f.name] = read_rows(path)
            except (DuplicateHeaderError, IngestFormatError):
                skipped.append(f.name)

        if skipped:
            st.warning(
                f"Skipped {', '.join(skipped)} — duplicate column names, a multi-sheet Excel "
                "file with no sheet chosen, or a malformed JSON/Excel shape. Fix and re-upload, "
                "or use the Analyze & Clean tab's auto-rename option first (CSV/TSV only)."
            )
```

- [ ] **Step 7: Verify live in the browser**

Run: `.venv/bin/pip install -e ".[dev,viewer]"` (picks up `openpyxl`/`xlrd` inside the app's own venv, needed since Tasks 1-5 only ran in the CLI's test environment which is the same venv, so this should already be a no-op confirming installation, but run it to be sure)

Start the app (this project's dev server config is `dataforensics` in `.claude/launch.json` at the Desktop level, or run directly):
```bash
PYTHONPATH="$(pwd)/.venv/lib/python3.12/site-packages:$(pwd)/src" /opt/homebrew/bin/python3.12 -m streamlit run app.py --server.headless true --server.port 8503
```

In the browser, on the Analyze & Clean tab:
1. Create a small `.json` test file with content `[{"id": "1", "age": 34}, {"id": "2", "age": 29}]`, upload it, confirm the Data dictionary table renders with `id`/`age` columns and no error.
2. Create a small single-sheet `.xlsx` test file (e.g. via a quick Python one-liner using `openpyxl`), upload it, confirm the same.
3. Create a small multi-sheet `.xlsx` test file, upload it, confirm the sheet-picker `selectbox` appears and switching sheets updates the Data dictionary table.
4. Confirm the existing CSV "Use bundled example" flow still works unchanged (regression check for the `raw_format == "delimited"` branches).

Expected: all four flows work with no unhandled exception/traceback in the app or in `preview_logs`.

- [ ] **Step 8: Run the full existing test suite once more (defense in depth — app.py has no direct tests, but confirm nothing else broke)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, zero failures

- [ ] **Step 9: Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
Accept JSON/Excel uploads in the Streamlit app

Both upload widgets (Analyze & Clean, Multi-File Relationships) now
accept .json/.xlsx/.xls. A multi-sheet Excel upload in the Analyze &
Clean tab shows a sheet picker before profiling; the Multi-File tab
surfaces the same case as a per-file skip with an explanatory
message, no per-file picker. The CSV-specific duplicate-header
auto-rename button and "ragged row" warning are now delimited-text
only, since neither applies to JSON/Excel's already-structured input.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: README — document the new formats and the Excel leading-zero limitation

**Files:**
- Modify: `README.md` (the "Quickstart," "Interactive app," "CLI reference," and "Known limitations" sections)

- [ ] **Step 1: Update the CLI reference's `scan`/`harmonize` blocks to mention `.json`/`.xlsx`/`.xls` and `--sheet`**

In the fenced code block under `## CLI reference (as actually implemented today)`, change:

```
dataforensics scan <file> [--rules schema.yaml] [--out-dir DIR]
    Read-only. Always writes <stem>.data_dictionary.{json,md}. If --rules is given, also
    writes <stem>.validation_report.{json,md}. Never writes to the input path.
    Exit 0 (clean or no --rules), 1 (validation errors found), 2 (malformed rules file),
    3 (malformed input file, e.g. a duplicate header column).
```

to:

```
dataforensics scan <file> [--rules schema.yaml] [--out-dir DIR] [--sheet NAME]
    Read-only. Accepts CSV, TSV, JSON (a top-level array of flat objects), or Excel
    (.xlsx/.xls). --sheet picks a sheet in a multi-sheet Excel workbook (required if the
    workbook has more than one -- scan refuses rather than guessing which one you meant);
    ignored for non-Excel input. Always writes <stem>.data_dictionary.{json,md}. If --rules
    is given, also writes <stem>.validation_report.{json,md}. Never writes to the input path.
    Exit 0 (clean or no --rules), 1 (validation errors found), 2 (malformed rules file),
    3 (malformed input file -- a duplicate header column, invalid JSON shape, or an
    unresolved multi-sheet Excel workbook).
```

Change:

```
dataforensics harmonize <file> --rules schema.yaml --output <path> [--execute]
    Single-file mode. Without --execute: dry run, writes nothing, just lists proposed
    transformations (footer-stripping warnings, if any, are printed on the dry run too, not
    just --execute). With --execute: applies the rules, writes <path> and
    <path>.manifest.json atomically. Refuses (exit 2) if --output equals the input path
    or if the rules file is malformed; exits 3 on a malformed input file or if a post-transform
    safety check fails (refuses to write rather than risk silent data loss).
```

to:

```
dataforensics harmonize <file> --rules schema.yaml --output <path> [--execute] [--sheet NAME]
    Single-file mode. Accepts CSV, TSV, JSON, or Excel input the same way scan does; --sheet
    picks a sheet in a multi-sheet Excel workbook. Without --execute: dry run, writes nothing,
    just lists proposed transformations (footer-stripping warnings, if any, are printed on the
    dry run too, not just --execute). With --execute: applies the rules, writes <path> and
    <path>.manifest.json atomically. Refuses (exit 2) if --output equals the input path
    or if the rules file is malformed; exits 3 on a malformed input file or if a post-transform
    safety check fails (refuses to write rather than risk silent data loss).
```

In the crosswalk-mode block, change the trailing sentence "Exits 2 if --output-dir collides..." paragraph's exit-3 clause from "exits 3 on a malformed input file" to "exits 3 on a malformed input file (including an unresolved multi-sheet Excel source -- --sheet is not available in crosswalk mode, so a multi-sheet source must be split or converted to a single-sheet file first)":

```
    path, a source has no --rules-map entry, or a source's filename stem has no matching
    entry under the crosswalk file's `sources:` key; exits 3 on a malformed input file
    (including an unresolved multi-sheet Excel source -- --sheet is not available in
    crosswalk mode, so a multi-sheet source must be split or converted to a single-sheet
    file first) or a failed safety check for any source (nothing is written for ANY source
    in that case -- see the two-pass validate-then-write design below).
```

- [ ] **Step 2: Update the Quickstart and Interactive app sections' upload/input wording**

In `## Interactive app (optional)`, change:

```
- **Analyze & Clean** — upload a CSV (or click "Use bundled example"), and it runs the full
```

to:

```
- **Analyze & Clean** — upload a CSV/TSV/JSON/Excel file (or click "Use bundled example"), and
  it runs the full
```

- [ ] **Step 3: Add a new "Known limitations" entry for Excel type coercion**

In `## Known limitations`, after the existing "Footer detection is not CSV-quote-aware..." paragraph, add a new paragraph:

```markdown

**Excel's own type coercion can destroy information before this tool ever sees the file.**
A spreadsheet cell typed as `007` (e.g. a FIPS or ZIP code) is stored by Excel as the number
`7` — the leading zero is gone before `openpyxl`/`xlrd` read it, and there is no way to
recover it afterward. This is a limitation of the Excel file format itself, not something
`dataforensics`'s `.xlsx`/`.xls` reader can detect or fix. If leading zeros matter, prefer a
CSV/TSV/JSON export of the same data, where the value is preserved as literal text.
```

- [ ] **Step 4: Verify the README renders sensibly**

Run: `.venv/bin/python -m pytest -q` (confirms nothing in the doc edit broke any doctest-like check — there are none currently, this is just a final regression gate before committing docs)
Expected: all pass, zero failures

Read the four edited sections back with `git diff README.md` and confirm no stray markdown fence or heading was broken.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Document JSON/Excel input support and the Excel leading-zero limitation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Notes (for the plan author, not a task to execute)

- **Spec coverage:** `detect_file_format`/`IngestFormatError` (Task 1), `read_json_rows` incl. all rejected shapes (Task 2), `read_excel_rows`/`list_excel_sheets` incl. multi-sheet error and `.xls` (Task 3), shared dispatch in `dictionary.py` (Task 4), CLI `--sheet` + exit-code mapping on `scan`/`harmonize` incl. crosswalk mode's narrower scope (Task 5), both app.py upload widgets + sheet picker (Task 6), README + known-limitation doc (Task 7). All spec sections are covered.
- **Placeholder scan:** no TBD/TODO; every step has literal code or an exact command.
- **Type consistency:** `sheet: str | None = None` is the same name and type everywhere it's threaded (`ingest.read_excel_rows`, `dictionary.read_rows`/`build_data_dictionary`, `cli._read_header_and_row_count`/`scan`/`harmonize`/`_harmonize_single_file`, `app.py`'s `sheet_choice` local). `IngestFormatError` and `DuplicateHeaderError` are always caught together as a tuple everywhere `DuplicateHeaderError` was previously caught alone in `cli.py`/`app.py`.
