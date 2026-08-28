from itertools import zip_longest
from pathlib import Path

from dataforensics.ingest import (
    check_header_has_no_duplicates,
    detect_delimiter,
    detect_file_format,
    read_excel_table,
    read_json_rows,
    read_source_lines,
    split_delimited_line,
    strip_footer,
)
from dataforensics.typing_guards import is_id_like_column, is_pii_like_column, parse_finite_float, preserves_leading_zero

# Never claim "PII-safe" or "HIPAA-compliant" here — the honest phrasing is
# "potential identifier pattern detected," which is a naming-convention
# heuristic, not a guarantee about the actual contents of the column.
_PII_MASK_MESSAGE = "[masked: potential identifier pattern detected]"

# Absolute floor on the cardinality cap. A pure "5% of N" cap collapses to 0
# or 1 for small samples (e.g. N=4 rows -> int(0.05*4) == 0), which would
# misclassify ordinary low-cardinality categoricals (like a 2-level "sex"
# column) as free text. The floor keeps small files usable while the
# "min(50, 5%)" ratio still dominates once N is large enough to matter.
_CARDINALITY_FLOOR = 10
_CARDINALITY_MAX = 50
_CARDINALITY_RATIO = 0.05

_TOP_CODE_SPIKE_THRESHOLD = 0.05


def _read_cleaned_lines(path: Path) -> tuple[list[str], str, list[str]]:
    raw_lines, _encoding = read_source_lines(path)
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, stripped = strip_footer(raw_lines, delimiter)
    return data_lines, delimiter, stripped


def count_stripped_footer_lines(path: Path) -> int:
    """How many lines strip_footer discarded as a non-data footer block
    for this delimited-text file (0 for a file where nothing was
    stripped, and 0 for a non-delimited format, since footer-stripping
    only applies to CSV/TSV). For display -- e.g. the Streamlit app
    surfaces this as a warning, matching the CLI's own long-standing
    stderr warning for the same event, so a genuine footer being
    stripped is never silent in the app either."""
    if detect_file_format(path) != "delimited":
        return 0
    _data_lines, _delimiter, stripped = _read_cleaned_lines(path)
    return len(stripped)


def _load_table(path: Path, sheet: str | None = None) -> tuple[list[str], list[list[str]]]:
    """Returns (header, body_rows) for any supported input format. The
    delimited-text branch is exactly what build_data_dictionary and
    read_rows already did inline before this function existed -- moved
    here unchanged so both functions share one implementation instead of
    two copies that could drift apart. The excel branch calls
    read_excel_table directly, since it already returns exactly this
    (header, body_rows) shape -- this also preserves a header-only sheet's
    schema (a real header row with zero data rows), which a genuinely
    empty sheet does not have. The json branch converts read_json_rows'
    list[dict[str, str]] into the same shape; JSON has no equivalent
    header-only case, since a JSON array's "header" is only ever the union
    of keys actually present in its elements -- an empty array genuinely
    has no header to preserve.
    """
    fmt = detect_file_format(path)
    if fmt == "delimited":
        data_lines, delimiter, _stripped = _read_cleaned_lines(path)
        if not data_lines:
            return [], []
        header = split_delimited_line(data_lines[0], delimiter)
        check_header_has_no_duplicates(header)
        body_rows = [split_delimited_line(line, delimiter) for line in data_lines[1:]]
        return header, body_rows

    if fmt == "excel":
        return read_excel_table(path, sheet=sheet)

    rows = read_json_rows(path)
    if not rows:
        return [], []
    header = list(rows[0].keys())
    body_rows = [[row[name] for name in header] for row in rows]
    return header, body_rows


def cardinality_cap(n_rows: int) -> int:
    """The same categorical-vs-free-text cardinality threshold used by
    build_data_dictionary's own category classification -- public so
    validation.py's rare-category suggestion heuristic can share it
    instead of using an independent, much more permissive threshold. A
    household-identifier-shaped column (e.g. ACS PUMS's SERIALNO: high
    but not maximal cardinality, since multiple people share one
    household's serial number) is real-world evidence this divergence
    isn't theoretical: dictionary.py correctly classifies it "free_text"
    once it exceeds this cap, but validation.py's old ad-hoc
    "unique_count < half the rows" threshold still called it categorical
    -- firing a misleading "rare category" suggestion on every household
    that happened to have exactly one person in this sample.
    """
    if n_rows <= 0:
        return _CARDINALITY_FLOOR
    ratio_cap = int(_CARDINALITY_RATIO * n_rows)
    return min(_CARDINALITY_MAX, max(_CARDINALITY_FLOOR, ratio_cap))


def build_data_dictionary(path: Path, include_raw_samples: bool = False, sheet: str | None = None) -> dict:
    header, body_rows = _load_table(path, sheet=sheet)
    if not header:
        return {}
    n_rows = len(body_rows)

    # zip_longest (not zip) so a row with fewer fields than the header
    # contributes an explicit null for its missing trailing columns instead
    # of silently vanishing from that column's value list, which would
    # understate null_count / non_null_pct for ragged real-world exports.
    columns: dict[str, list[str]] = {name: [] for name in header}
    for row in body_rows:
        for name, value in zip_longest(header, row, fillvalue=""):
            if name in columns:
                columns[name].append(value)

    cap = cardinality_cap(n_rows)

    result = {}
    for name, raw_values in columns.items():
        null_count = sum(1 for v in raw_values if v == "")
        non_null_values = [v for v in raw_values if v != ""]
        non_null_pct = round(100.0 * (n_rows - null_count) / n_rows, 4) if n_rows else 0.0
        unique_values = set(non_null_values)
        unique_count = len(unique_values)
        zero_count = sum(1 for v in non_null_values if v == "0")

        id_like = is_id_like_column(name) or preserves_leading_zero(non_null_values)
        pii_like = is_pii_like_column(name)
        mask_pii = pii_like and not include_raw_samples

        if id_like:
            category = "id"
            levels = None
        elif unique_count <= cap:
            category = "categorical"
            levels = sorted(unique_values)
        else:
            category = "free_text"
            levels = None

        numeric_values = []
        # Skip numeric parsing for masked PII-like columns too: outliers and
        # top_code_spike below carry an actual raw value from the column
        # (the max value / the flagged indices' underlying magnitude), which
        # would leak a real identifier value (e.g. a recurring MRN) around
        # the masking done just below. When include_raw_samples=True the
        # column behaves exactly like any other, so numeric detection still
        # runs.
        if category != "id" and not mask_pii:
            for v in non_null_values:
                parsed = parse_finite_float(v)
                if parsed is None:
                    numeric_values = []
                    break
                numeric_values.append(parsed)

        outliers = detect_outliers(numeric_values) if numeric_values else None
        top_code_spike = detect_top_code_spike(numeric_values) if numeric_values else None

        if mask_pii:
            levels = _PII_MASK_MESSAGE

        result[name] = {
            "dtype": "Utf8",
            "category": category,
            "non_null_pct": non_null_pct,
            "unique_count": unique_count,
            "is_zero_variance": unique_count == 1,
            "zero_count": zero_count,
            "null_count": null_count,
            "levels": levels,
            "outliers": outliers,
            "top_code_spike": top_code_spike,
        }

    return result


def detect_outliers(values: list[float]) -> dict:
    if len(values) < 4:
        return {"method": "IQR", "outlier_count": 0, "outlier_indices": []}

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[(n - 1) // 4]
    q3 = sorted_vals[(3 * (n - 1)) // 4]
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    median = sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2

    indices = [i for i, v in enumerate(values) if v < lower or v > upper]
    return {
        "method": "IQR",
        "outlier_count": len(indices),
        "outlier_indices": indices,
        # Summary statistics for DISPLAY (e.g. "Median $62,400 / IQR
        # $31,200 / Maximum $1,240,000") -- not used by the detection
        # logic above, which only needs q1/q3/iqr as local values.
        "median": median,
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "min": sorted_vals[0],
        "max": sorted_vals[-1],
    }


def read_rows(path: Path, sheet: str | None = None) -> list[dict]:
    header, body_rows = _load_table(path, sheet=sheet)
    if not header:
        return []
    return [dict(zip_longest(header, row, fillvalue="")) for row in body_rows]


def detect_top_code_spike(values: list[float]) -> dict | None:
    if not values:
        return None
    max_val = max(values)
    fraction = sum(1 for v in values if v == max_val) / len(values)
    if fraction >= _TOP_CODE_SPIKE_THRESHOLD and fraction > 1 / len(values):
        return {"value": max_val, "fraction": round(fraction, 4)}
    return None


def find_outlier_evidence(rows: list[dict], column: str) -> list[tuple[int, str]]:
    """Real (row_index, raw_value) pairs for the values in `column` that
    detect_outliers would flag, for showing genuine evidence ("row 183 ->
    age = 300") instead of just a count.

    detect_outliers' own `outlier_indices` are positions within the
    filtered non-null-and-numeric-parseable value list it was given, NOT
    row indices into the original dataset -- a documented, deliberate
    scope boundary of that function (it doesn't have the original rows to
    map back to). This function re-derives the same non-null/numeric
    filtering build_data_dictionary uses (so the same values end up
    flagged) while keeping each value's real row index alongside it.

    Returns [] if the column isn't uniformly numeric (any non-null value
    fails float() parsing) -- the same "all or nothing" numeric detection
    build_data_dictionary itself uses, so evidence is never shown for a
    column the dictionary didn't actually treat as numeric.
    """
    non_null = [(i, row.get(column, "")) for i, row in enumerate(rows) if row.get(column, "") != ""]
    values_only = [parse_finite_float(v) for _, v in non_null]
    if not values_only or any(v is None for v in values_only):
        return []
    result = detect_outliers(values_only)
    return [non_null[pos] for pos in result["outlier_indices"]]


def find_top_code_evidence(rows: list[dict], column: str, top_value: float) -> list[tuple[int, str]]:
    """Real (row_index, raw_value) pairs for the rows sitting at
    `top_value` (the value detect_top_code_spike flagged as a likely
    top-coding ceiling), for the same "show the actual rows" reason as
    find_outlier_evidence.
    """
    evidence = []
    for i, row in enumerate(rows):
        raw = row.get(column, "")
        if raw == "":
            continue
        numeric = parse_finite_float(raw)
        if numeric is None:
            continue
        if numeric == top_value:
            evidence.append((i, raw))
    return evidence
