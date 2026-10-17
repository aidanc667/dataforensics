"""Codebooks: the meaning of coded values ("marital_status 3 = Widowed").

Survey and administrative files store answers as codes; without the
codebook, an insight like "marital_status = 3 are 1.8x as likely ..." is
unreadable. A codebook here is a small CSV with three columns -- column,
value, label -- one row per code:

    column,value,label
    marital_status,1,Married
    marital_status,3,Widowed

Header names are matched case-insensitively, and the common synonyms
(variable/field for column, code for value, meaning/description for label)
are accepted. Labels only change how values are displayed; they never
touch the data.
"""

import csv
import io

_COLUMN_NAMES = {"column", "variable", "field", "column_name", "variable_name"}
_VALUE_NAMES = {"value", "code", "values", "codes"}
_LABEL_NAMES = {"label", "meaning", "description", "value_label"}
MAX_CODEBOOK_BYTES = 2_000_000


class CodebookError(ValueError):
    """The codebook file can't be read; the message says why, for the user."""


def _find(header: list[str], names: set[str], what: str) -> int:
    for i, h in enumerate(header):
        if h.strip().lower().replace(" ", "_") in names:
            return i
    raise CodebookError(f"No {what} column in the codebook header (expected one of: {', '.join(sorted(names))}).")


def parse_codebook(data: bytes) -> dict[str, dict[str, str]]:
    """{column: {code: label}} from codebook CSV bytes. Raises CodebookError
    for an empty, oversized, undecodable or malformed file, and for a code
    given two different labels."""
    if len(data) > MAX_CODEBOOK_BYTES:
        raise CodebookError("The codebook is larger than 2 MB — that's more than a list of code labels.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise CodebookError("The codebook isn't readable text (expected a UTF-8 CSV).") from exc
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        raise CodebookError("The codebook is empty.")
    ci = _find(header, _COLUMN_NAMES, "column")
    vi = _find(header, _VALUE_NAMES, "value")
    li = _find(header, _LABEL_NAMES, "label")
    width = max(ci, vi, li) + 1
    labels: dict[str, dict[str, str]] = {}
    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < width:
            raise CodebookError(f"Line {line_no} of the codebook has {len(row)} fields; expected at least {width}.")
        column, value, label = row[ci].strip(), row[vi].strip(), row[li].strip()
        if not column or not value or not label:
            raise CodebookError(f"Line {line_no} of the codebook is missing a column name, value or label.")
        existing = labels.setdefault(column, {}).get(value)
        if existing is not None and existing != label:
            raise CodebookError(f"{column} = {value} has two labels in the codebook: '{existing}' and '{label}'.")
        labels[column][value] = label
    if not labels:
        raise CodebookError("The codebook has a header but no codes.")
    return labels
