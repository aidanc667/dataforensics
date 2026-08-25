from itertools import zip_longest
from pathlib import Path

from rdh.ingest import detect_delimiter, detect_encoding, strip_footer
from rdh.typing_guards import is_id_like_column, preserves_leading_zero

# Absolute floor on the cardinality cap. A pure "5% of N" cap collapses to 0
# or 1 for small samples (e.g. N=4 rows -> int(0.05*4) == 0), which would
# misclassify ordinary low-cardinality categoricals (like a 2-level "sex"
# column) as free text. The floor keeps small files usable while the
# "min(50, 5%)" ratio still dominates once N is large enough to matter.
_CARDINALITY_FLOOR = 10
_CARDINALITY_MAX = 50
_CARDINALITY_RATIO = 0.05


def _read_cleaned_lines(path: Path) -> tuple[list[str], str]:
    encoding = detect_encoding(path)
    raw_lines = path.read_text(encoding=encoding).splitlines()
    delimiter = detect_delimiter(raw_lines[:10])
    data_lines, _stripped = strip_footer(raw_lines, delimiter)
    return data_lines, delimiter


def _cardinality_cap(n_rows: int) -> int:
    if n_rows <= 0:
        return _CARDINALITY_FLOOR
    ratio_cap = int(_CARDINALITY_RATIO * n_rows)
    return min(_CARDINALITY_MAX, max(_CARDINALITY_FLOOR, ratio_cap))


def build_data_dictionary(path: Path) -> dict:
    data_lines, delimiter = _read_cleaned_lines(path)
    header = data_lines[0].split(delimiter)
    body_rows = [line.split(delimiter) for line in data_lines[1:]]
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

    cardinality_cap = _cardinality_cap(n_rows)

    result = {}
    for name, raw_values in columns.items():
        null_count = sum(1 for v in raw_values if v == "")
        non_null_values = [v for v in raw_values if v != ""]
        non_null_pct = round(100.0 * (n_rows - null_count) / n_rows, 4) if n_rows else 0.0
        unique_values = set(non_null_values)
        unique_count = len(unique_values)
        zero_count = sum(1 for v in non_null_values if v == "0")

        id_like = is_id_like_column(name) or preserves_leading_zero(non_null_values)

        if id_like:
            category = "id"
            levels = None
        elif unique_count <= cardinality_cap:
            category = "categorical"
            levels = sorted(unique_values)
        else:
            category = "free_text"
            levels = None

        result[name] = {
            "dtype": "Utf8",
            "category": category,
            "non_null_pct": non_null_pct,
            "unique_count": unique_count,
            "is_zero_variance": unique_count == 1,
            "zero_count": zero_count,
            "null_count": null_count,
            "levels": levels,
        }

    return result
