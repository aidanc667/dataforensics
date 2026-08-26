from pathlib import Path
import datetime
import json

from charset_normalizer import from_path

_CANDIDATE_DELIMITERS = [",", "\t", ";", "|"]
_FOOTER_MISMATCH_RUN = 2


class DuplicateHeaderError(Exception):
    """Raised when a CSV header row contains the same column name more than
    once. Every header-parsing site in this codebase (build_data_dictionary,
    read_rows, and cli._read_header_and_row_count) shares this check via
    check_header_has_no_duplicates -- without it, dict-based row/column
    construction (dict(zip(...)), {name: [] for name in header}) silently
    collapses same-named columns, and earlier-occurring columns' data is
    lost with no error and exit 0. This is a malformed-input-file condition,
    not a config problem; callers should map it to a runtime/IO failure
    (exit code 3), not a config error (exit code 2)."""


def check_header_has_no_duplicates(header: list[str]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in header:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    if duplicates:
        names = ", ".join(f"'{d}'" for d in duplicates)
        raise DuplicateHeaderError(
            f"Duplicate column name(s) in header: {names} — every same-named "
            "column after the first would silently lose its data if this were "
            "allowed to proceed"
        )


def deduplicate_header(header: list[str]) -> list[str]:
    """Deterministically rename duplicate header entries by appending a
    positional suffix (second 'name' -> 'name_2', third -> 'name_3', ...).

    This is NOT called anywhere in the default CLI path -- `scan`/`harmonize`
    still refuse via `check_header_has_no_duplicates` by default, since a
    silent rename could surprise a scripted/automated caller. It exists for
    an interactive caller (e.g. a UI) to offer as an explicit, opt-in,
    clearly-logged remediation: renaming is lossless (every column survives
    under a distinguishable name) and fully deterministic, unlike merging or
    dropping, so it's safe to offer as a one-click fix -- but only ever with
    the human choosing it, never automatically.
    """
    seen_counts: dict[str, int] = {}
    result = []
    for name in header:
        seen_counts[name] = seen_counts.get(name, 0) + 1
        if seen_counts[name] == 1:
            result.append(name)
        else:
            result.append(f"{name}_{seen_counts[name]}")
    return result


def detect_encoding(path: Path) -> str:
    result = from_path(str(path)).best()
    if result is None:
        return "utf-8"

    encoding = result.encoding
    # Normalize encoding name spelling to use hyphens instead of underscores
    # (e.g. "utf_8" -> "utf-8"). This only reformats the string charset_normalizer
    # returned; it never changes which encoding is being reported.
    encoding = encoding.replace("_", "-")

    return encoding


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


def strip_footer(lines: list[str], delimiter: str) -> tuple[list[str], list[str]]:
    """Split ``lines`` into (data_lines, stripped_lines) by looking for a run
    of consecutive lines whose delimiter-count disagrees with the header's
    (e.g. a CDC WONDER-style "Query Parameters:" footer block).

    Known limitation: this field-count heuristic uses ``line.count(delimiter)``
    on the raw line, which is NOT CSV-quote-aware -- there is no real CSV
    dialect parser here (e.g. Python's ``csv`` module), just a hand-rolled
    split-by-delimiter count. A genuine data row containing a quoted
    delimiter (e.g. a comma-delimited file with a value like
    ``"Delta Clinic, North"``) has a higher raw delimiter count than the
    header and can be misclassified as a footer line -- this is a plausible
    trigger, not an exotic edge case: two consecutive rows with a quoted
    delimiter in any free-text column (site names, addresses, notes) is
    enough. Once triggered, this function does NOT drop only the
    misclassified line(s): it treats EVERYTHING from the first detected
    mismatch to end-of-file as footer and returns it all in
    ``stripped_lines``. This is a truncation of the rest of the file, not a
    single-row exclusion -- on a file where the mismatch starts near the
    top, the majority of the dataset can end up in ``stripped_lines``.
    Properly fixing this would mean rewriting the parser to be
    CSV-quote-aware, which is a larger architectural change; callers should
    instead surface ``stripped_lines`` to the user (see
    ``cli._warn_if_footer_stripped``) rather than discarding it silently, so
    a truncation like this is at least visible instead of silent.
    """
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
