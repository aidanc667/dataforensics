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
import re

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


# Labels that say the value is not an answer: survey "Don't know" / "Refused"
# codes (BRFSS 7/9, 77/99). These phrases count anywhere in a label...
_NO_ANSWER_PHRASE = re.compile(
    r"\b(don[’']?t know|not sure|refused|no answer|not stated|not reported|prefer not|declined to (answer|respond|state))\b",
    re.IGNORECASE,
)
# ...these single words only as a whole label (or one "/"-separated part of
# it): "Unknown" is a no-answer code, "Status unknown" or "All teeth missing"
# is a real answer. Bare "Declined" (an application outcome) never counts.
_NO_ANSWER_WORD = re.compile(r"^(unknown|missing|blank)$", re.IGNORECASE)
# A range row ("9023-9352") whose label gives a unit and an offset:
# "Weight in kilograms (value minus 9000)".
_RANGE_VALUE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")
_OFFSET = re.compile(r"value\s*(?:minus|-|−)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_LABEL_UNITS = {"kg": re.compile(r"\b(kg|kilograms?)\b", re.IGNORECASE), "cm": re.compile(r"\b(cm|centimet(er|re)s?)\b", re.IGNORECASE)}
_KG_TO_LB = 2.20462


def _means_no_answer(label: str) -> bool:
    if _NO_ANSWER_PHRASE.search(label):
        return True
    return any(_NO_ANSWER_WORD.match(part.strip()) for part in re.split(r"[/;]", label))


def no_answer_codes(labels: dict[str, dict[str, str]]) -> dict[str, set[str]]:
    """{column: codes whose codebook label means no answer} -- "77" labelled
    "Don't know / Not sure". Range rows are left out."""
    codes: dict[str, set[str]] = {}
    for column, mapping in labels.items():
        found = {v for v, label in mapping.items() if not _RANGE_VALUE.match(v) and _means_no_answer(label)}
        if found:
            codes[column] = found
    return codes


def coded_unit_ranges(labels: dict[str, dict[str, str]]) -> dict[str, tuple[float, float, float, str]]:
    """{column: (low, high, offset, unit)} for a range row whose label names a
    unit (kg, cm) and an offset -- BRFSS records weights in kilograms as
    9000 + kg inside a pounds column ("9023-9352": "Weight in kilograms
    (value minus 9000)")."""
    ranges: dict[str, tuple[float, float, float, str]] = {}
    for column, mapping in labels.items():
        for value, label in mapping.items():
            bounds, offset = _RANGE_VALUE.match(value), _OFFSET.search(label)
            unit = next((u for u, pattern in _LABEL_UNITS.items() if pattern.search(label)), None)
            if bounds and offset and unit:
                low, high = float(bounds.group(1)), float(bounds.group(2))
                if low < high:
                    ranges[column] = (low, high, float(offset.group(1)), unit)
    return ranges


def convert_coded_unit(raw: str, spec: tuple[float, float, float, str], column: str) -> str | None:
    """`raw` in the column's own unit, or None when it's outside the range or
    the column's unit can't be told from its name. Pounds (weight_lbs) from
    kg; BRFSS-style feet-and-inches (height_ft_in, 511 = 5 ft 11 in) or
    inches from cm. Whole units, as the column records them."""
    low, high, offset, unit = spec
    try:
        number = float(raw.strip())
    except ValueError:
        return None
    if not low <= number <= high:
        return None
    measure = number - offset
    name = column.lower()
    if unit == "kg" and re.search(r"(^|_)(lb|lbs|pounds?)(_|$)", name):
        return str(round(measure * _KG_TO_LB))
    if unit == "cm" and re.search(r"(^|_)ft_?in(_|$)", name):
        inches = round(measure / 2.54)
        return f"{inches // 12}{inches % 12:02d}"
    if unit == "cm" and re.search(r"(^|_)(in|inch|inches)(_|$)", name):
        return str(round(measure / 2.54))
    return None
