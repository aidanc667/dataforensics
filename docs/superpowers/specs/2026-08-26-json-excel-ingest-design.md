# JSON & Excel Input Support — Design

## Problem

DataForensics currently accepts only CSV/TSV as input. Research-data exports
routinely arrive as JSON (API dumps, NoSQL exports) or Excel workbooks
(the default export format from most survey/EDC tools). Users have to
convert those to CSV by hand before the tool is useful to them at all.

## Scope

Both the CLI (`scan`, `harmonize`, `report`) and the Streamlit app's data
upload widgets (Analyze & Clean tab, Multi-File Relationships tab) gain
`.json`, `.xlsx`, and `.xls` support. The two *existing* JSON uploaders in
the app (fingerprint-compare, report-viewer) read the tool's own JSON
output, not tabular data — they are out of scope and stay untouched.

## Architecture

The whole downstream engine (validation, harmonize, investigate) already
operates on a format-agnostic `list[dict[str, str]]` of rows. Today only
`dictionary.py`'s `read_rows()`/`build_data_dictionary()` and
`ingest.py`'s delimited-text helpers produce that shape. This design adds
two more producers of the same shape and a small router in front of them;
nothing downstream changes.

### `ingest.py` additions

- `detect_file_format(path: Path) -> str` — returns `"delimited"`,
  `"json"`, or `"excel"` by file extension. `.csv`/`.tsv`/anything
  unrecognized → `"delimited"` (today's behavior, unchanged). `.json` →
  `"json"`. `.xlsx`/`.xls` → `"excel"`.

- `class IngestFormatError(Exception)` — new exception for malformed
  JSON/Excel input, parallel to the existing `DuplicateHeaderError`. Like
  that error, it signals a malformed-input-file condition (exit code 3 in
  the CLI), not a config problem.

- `read_json_rows(path: Path) -> list[dict[str, str]]` — requires the
  top-level JSON value to be an array; every element must be a JSON
  object (not a scalar, not a nested array); every value inside each
  object must be a JSON scalar (string, number, boolean, or null) — an
  object or array as a *value* is rejected, since flattening it would
  mean guessing a convention (dot-path keys? repeated rows? first
  element only?) rather than following one the user specified. Any
  violation raises `IngestFormatError` with a message that names the
  specific problem (e.g. "element 3 is not a JSON object" / "column
  'tags' at row 5 is an array, not a single value").
  The header is the union of keys across all objects, in first-seen
  order (objects need not all share identical keys — a missing key
  becomes `""` for that row, matching how a ragged CSV row already
  behaves via `zip_longest` elsewhere in this codebase). Unlike a CSV/
  Excel header row, this union can never itself contain a duplicate
  name, so `check_header_has_no_duplicates()` is not applicable here —
  it stays specific to the delimited-text and Excel readers, where the
  header comes from literal, independently-writable cells/fields.

- `read_excel_rows(path: Path, sheet: str | None = None) -> list[dict[str, str]]`
  — `.xlsx` via `openpyxl` (`data_only=True`, so formulas resolve to
  their last-computed value, not the formula text), `.xls` via `xlrd`.
  If the workbook has more than one sheet and `sheet` is not given,
  raises `IngestFormatError` listing the sheet names — never silently
  picks one. First row of the chosen sheet is the header.

- `_stringify_cell(value) -> str` — shared by both new readers so JSON
  and Excel produce identical text for equivalent values:
  - `None` → `""` (matches an empty/blank CSV cell)
  - `bool` → `"true"` / `"false"` (lowercase, JSON's own spelling —
    checked before the `int` case, since `bool` is a subclass of `int`
    in Python)
  - `int`/`float` → `str(value)` (`5` → `"5"`, `5.5` → `"5.5"`)
  - `datetime.date`/`datetime.datetime` (Excel only) → ISO 8601
    (`"2024-01-15"` / `"2024-01-15T00:00:00"`). This is deliberately
    *not* run back through the ambiguous-date detector's MM/DD-vs-DD/MM
    guessing — the type was never ambiguous, Excel just already knows
    it's a date, so treating it as unambiguous ISO text is correct, not
    a gap.
  - `str` → unchanged

- `read_excel_rows()` applies `check_header_has_no_duplicates()` to the
  sheet's header row, same as the delimited-text path (`read_json_rows()`
  does not — see above).

### `dictionary.py` changes

`read_rows()` and `build_data_dictionary()` call `detect_file_format()`
first and dispatch to the delimited/json/excel reader accordingly. The
delimited branch is exactly today's code, unchanged. Encoding detection,
delimiter sniffing, and footer-stripping are delimited-text-only concerns
and are skipped entirely for JSON/Excel (they load fully-structured data,
so there is no footer or encoding to sniff).

### CLI (`cli.py`)

- `scan`, `harmonize`, and `report` accept `.json`/`.xlsx`/`.xls` paths
  with no new flags required for the common case.
- New `--sheet NAME` option on `scan` and `harmonize` (ignored, not
  rejected, when the input isn't Excel — keeps command-line ergonomics
  simple when scripting over mixed input types).
- `IngestFormatError` maps to exit code 3, same family as
  `DuplicateHeaderError`.

### Streamlit app (`app.py`)

- The Analyze & Clean tab's `st.file_uploader` (line ~195) and the
  Multi-File Relationships tab's uploader (line ~596) both get
  `type=["csv", "tsv", "json", "xlsx", "xls"]`.
- When an uploaded `.xlsx`/`.xls` file has more than one sheet, an
  `st.selectbox` appears listing the sheet names before the file is
  processed; single-sheet workbooks skip straight to investigation, same
  as CSV today.
- The existing fingerprint-compare and report-viewer JSON uploaders are
  unchanged (different feature, different JSON shape).

### Dependencies

`openpyxl` and `xlrd` added to `pyproject.toml`'s core `dependencies` (not
`viewer`/`dev` extras, since the CLI needs them too).

## Known limitation (documented in README, not solved)

Excel's own type coercion can destroy information before this tool ever
sees the file — e.g. a FIPS/ZIP code typed as `007` in a spreadsheet cell
is stored by Excel as the number `7`, and the leading zero is gone before
`openpyxl` reads it. This is unrecoverable after the fact and is called
out as a known limitation, the same way the existing CSV footer-stripping
quote-unawareness is already documented rather than silently patched
over.

## Testing

- `tests/unit/test_ingest_detection.py` (or a new
  `test_ingest_json_excel.py`): `detect_file_format` for each extension;
  `read_json_rows` happy path, non-array top level, non-object element,
  nested object/array value, ragged keys across objects, duplicate keys;
  `read_excel_rows` happy path, multi-sheet-without-`sheet`-arg error,
  explicit `sheet` argument, `.xls` legacy path; `_stringify_cell` for
  each value kind (None, bool, int, float, date, datetime, str).
- `tests/fixtures/sample.json` and `tests/fixtures/sample.xlsx` (mirroring
  the shape of the existing `fixtures/sample.csv`) plus a
  `sample_multisheet.xlsx` fixture for the error-path test.
- Integration tests: `scan` and `harmonize` run end-to-end against the new
  JSON and Excel fixtures, asserting the same data dictionary shape as
  the equivalent CSV fixture produces.
- One CLI test for `--sheet` selecting a non-default sheet correctly.

## Out of scope for this change

- NDJSON / JSON Lines input.
- Auto-unwrapping a JSON object with a nested array under some key.
- Auto-selecting a sheet in a multi-sheet workbook.
- Recovering Excel-destroyed leading zeros or any other Excel type
  coercion.
- `.xlsb` (binary Excel) support.
