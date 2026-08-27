from itertools import zip_longest
from pathlib import Path

from dataforensics.ingest import (
    check_header_has_no_duplicates,
    detect_delimiter,
    detect_encoding,
    detect_file_format,
    read_excel_table,
    read_json_rows,
    split_delimited_line,
    strip_footer,
)
from dataforensics.typing_guards import is_id_like_column, is_pii_like_column, preserves_leading_zero

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


def _read_cleaned_lines(path: Path) -> tuple[list[str], str]:
    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, _stripped = strip_footer(raw_lines, delimiter)
    return data_lines, delimiter


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
        data_lines, delimiter = _read_cleaned_lines(path)
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
                try:
                    numeric_values.append(float(v))
                except ValueError:
                    numeric_values = []
                    break

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

    indices = [i for i, v in enumerate(values) if v < lower or v > upper]
    return {"method": "IQR", "outlier_count": len(indices), "outlier_indices": indices}


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
