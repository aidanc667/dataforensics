"""Approval-gated "safe fixes": deterministic, value-level repairs for
problems the investigation flags but that apply_transformations (missing-
value codes and category mappings) doesn't cover.

Each fix is a pure function of one cell value (plus parameters the
analyst approved), so it's predictable, repeatable, and idempotent:
running the same fixes again on already-fixed output changes nothing.
Every changed cell is recorded as a mutation in the same shape
harmonize.apply_transformations uses, so it lands in the same audit
trail. Nothing here runs unless the caller passes it in `fixes`.
"""

import math
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import partial

from dataforensics.advanced_checks import excel_serial_to_iso
from dataforensics.codebook import convert_coded_unit
from dataforensics.config_schema import find_chained_keys, find_mapping_conflicts
from dataforensics.harmonize import apply_transformations, compute_safety_report
from dataforensics.investigate import (
    PII_CONTENT_PATTERNS,
    find_fips_like_columns,
    find_zip_like_columns,
    repair_mojibake,
)
from dataforensics.typing_guards import is_pii_like_column, parse_finite_float

PII_MASK_PLACEHOLDER = "[masked: potential identifier pattern detected]"

# Order matters: encoding repair goes first (repairing "Annâ€‹" yields a
# real zero-width space, "ï»¿x" a BOM), then invisible characters (an NBSP
# becomes a normal space), then whitespace, so each later fix cleans up
# what an earlier one exposed and re-running is a no-op; then numeric
# parsing, then fixes that need a clean number to work on. The judgment
# calls come last: outliers are judged after any unit conversion (a kg
# value converted to lb is no longer an outlier), personal data is masked
# in the final text, and missing values are filled after everything that
# could empty a cell (a mapped missing-value code, a blanked outlier).
FIX_ORDER = (
    "mojibake", "invisible", "whitespace", "numeric_format", "leading_zeros", "excel_dates", "date_iso",
    "coded_units", "units", "outliers", "pii_mask", "impute",
)
# Fixes that need a person's judgment -- never ticked by "Approve all
# recommended fixes".
JUDGMENT_FIXES = ("outliers", "pii_mask", "impute")
# What a missing-value code in a text column is mapped to by default (a
# number column gets an empty cell). Still missing, and scored as missing.
MISSING_LABEL = "Missing"

FIX_LABELS = {
    "invisible": "Remove hidden/invisible characters",
    "whitespace": "Trim extra spaces and tabs",
    "mojibake": "Repair encoding corruption",
    "numeric_format": "Convert formatted numbers to plain numbers",
    "leading_zeros": "Restore leading zeros",
    "excel_dates": "Convert Excel date numbers to dates",
    "date_iso": "Standardize dates to YYYY-MM-DD",
    "coded_units": "Convert values the codebook records in another unit",
    "units": "Convert mixed units to one unit",
    "outliers": "Treat outliers",
    "pii_mask": "Mask personal data in text",
    "impute": "Fill missing values",
}

_ZERO_WIDTH = re.compile("[​‌‍‎‏﻿⁠\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DOUBLED_SPACE = re.compile(r"\s{2,}|\t")
_DECORATED_NUMBER = re.compile(r"^([\$€£])?\s*(-?[\d,]*\.?\d+)\s*([kKmMbB])?$")
_MAGNITUDE = {"k": Decimal(1_000), "m": Decimal(1_000_000), "b": Decimal(1_000_000_000)}


def fix_invisible(value: str) -> str:
    """Non-breaking spaces become ordinary spaces; zero-width characters,
    byte-order marks and control characters are removed. Tabs and
    newlines are kept -- they're legitimate inside a quoted text field."""
    return _ZERO_WIDTH.sub("", value.replace("\xa0", " "))


def fix_whitespace(value: str) -> str:
    """Strip leading/trailing whitespace and turn any run of 2+
    whitespace characters (spaces, tabs, newlines), or a single tab, into
    one space -- the same pattern detect_whitespace_anomalies flags, so an
    approved fix clears every value it was shown for."""
    return _DOUBLED_SPACE.sub(" ", value.strip())


def fix_mojibake(value: str) -> str:
    repaired = repair_mojibake(value)
    return value if repaired is None else repaired


def _format_decimal(number: Decimal) -> str:
    """Plain notation, no exponent, no trailing zeros ("50000", "1.5")."""
    text = format(number.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def fix_numeric_format(value: str) -> str:
    """"$50,000" -> "50000", "50k" -> "50000", "1.2M" -> "1200000".
    Percentages are deliberately left alone: whether "45%" should become
    45 or 0.45 depends on the rest of the column, which a value-level fix
    can't know. Anything that isn't a decorated number is returned as-is."""
    stripped = value.strip()
    if parse_finite_float(stripped) is not None:
        return value
    match = _DECORATED_NUMBER.match(stripped)
    if not match:
        return value
    _symbol, digits, suffix = match.groups()
    try:
        number = Decimal(digits.replace(",", ""))
    except InvalidOperation:
        return value
    if suffix:
        number *= _MAGNITUDE[suffix.lower()]
    return _format_decimal(number)


def fix_excel_date(value: str) -> str:
    """"45000" -> "2023-03-15" (Excel's 1900 date system). Only whole
    numbers in the plausible serial range convert; anything else -- real
    dates included -- is returned unchanged."""
    converted = excel_serial_to_iso(value.strip())
    return value if converted is None else converted


def fix_leading_zeros(value: str, width: int) -> str:
    """Left-pad an all-digit code to `width` ("8001" -> "08001" for a
    5-digit FIPS/ZIP). Leaves anything non-numeric or already that long."""
    stripped = value.strip()
    if stripped.isdigit() and len(stripped) < width:
        return stripped.zfill(width)
    return value


def fix_date_iso(value: str, source_format: str) -> str:
    """Re-write a date in the analyst-declared `source_format` (e.g.
    "%d/%m/%Y") as YYYY-MM-DD. A value that doesn't parse in that format
    is left unchanged rather than guessed at. Dash-separated values
    ("03-04-2021") are read with the same field order as the declared
    slash format, since the ambiguous-date check treats both alike."""
    text = value.strip()
    for fmt in dict.fromkeys((source_format, source_format.replace("/", "-"))):
        try:
            return datetime.strptime(text, fmt).date().isoformat()  # noqa: DTZ007 -- a date, no time zone
        except ValueError:
            continue
    return value


def fix_units(
    value: str, *, boundary: float, minority_side: str, factor: float, minority_min: float, minority_max: float
) -> str:
    """Convert a value in the minority unit cluster to the majority's unit.
    The parameters come from detect_unit_inconsistency: values <= boundary
    are the "low" cluster, and only values within [minority_min,
    minority_max] are converted -- a missing-value code (9999) or a typo
    far outside the minority cluster is left alone rather than turned into
    a plausible-looking measurement. A high-side minority (e.g. pounds in
    a kilogram column) is divided by `factor`; a low-side minority is
    multiplied. Everything else is returned unchanged. Rounded to 2
    decimals."""
    parsed = parse_finite_float(value.strip())
    if parsed is None or parsed <= 0 or not (minority_min <= parsed <= minority_max):
        return value
    is_low = parsed <= boundary
    if (minority_side == "low") != is_low:
        return value
    converted = parsed * factor if minority_side == "low" else parsed / factor
    return _format_decimal(Decimal(str(round(converted, 2))))


def fix_outliers(value: str, *, action: str, lower: float, upper: float, keep: list[str]) -> str:
    """A numeric value outside [lower, upper] is capped to the nearer limit
    (action "cap") or emptied (action "blank"). Values in `keep` -- the
    column's missing-value codes, like 9999 -- are never touched, so a code
    isn't turned into a plausible-looking number. Non-numbers are left as-is."""
    if value.strip() in keep:
        return value
    parsed = parse_finite_float(value.strip())
    if parsed is None or lower <= parsed <= upper:
        return value
    if action == "blank":
        return ""
    return _format_decimal(Decimal(str(lower if parsed < lower else upper)))


_PII_REPLACEMENTS = {"SSN": "[SSN removed]", "email address": "[email removed]", "phone number": "[phone removed]"}


def fix_pii_mask(value: str) -> str:
    """Replace SSNs, email addresses and phone numbers inside text with a
    marker ("call me at [phone removed]"), using the same patterns the
    personal-data check flags. The rest of the text is kept."""
    for label, pattern in PII_CONTENT_PATTERNS:
        value = pattern.sub(_PII_REPLACEMENTS[label], value)
    return value


_CODE_MAX_LEVELS = 12
_CODE_MAX_LEVELS_LARGE = 20


def looks_like_codes(values: list[str]) -> bool:
    """Whole numbers drawn from a short, repeating list: category codes or a
    rating scale -- employment status 3, satisfaction 1-5, income bracket
    1-11 plus 77/99 -- not measurements. A median of them means nothing, and
    an IQR "outlier" among them is a valid answer. "Short" is
    `_CODE_MAX_LEVELS` distinct values (with some repeating), or up to
    `_CODE_MAX_LEVELS_LARGE` when each value repeats ~10 times on average."""
    numbers = [parse_finite_float(v) for v in values]
    if not values or not all(n is not None and n == int(n) for n in numbers):
        return False
    distinct = len(set(values))
    return (distinct <= _CODE_MAX_LEVELS and distinct < len(values)) or (
        distinct <= _CODE_MAX_LEVELS_LARGE and distinct * 10 <= len(values)
    )


def impute_fill_value(
    rows: list[dict], column: str, exclude: set[str] | None = None, *, as_category: bool = False
) -> tuple[str, str] | None:
    """(method, value) to fill a column's empty cells with: the median for
    a column of measurements, otherwise the most common value (ties broken
    alphabetically). Whole numbers drawn from a short, repeating list
    (`_CODE_MAX_LEVELS` distinct values or fewer) are category codes -- employment status 3,
    income bracket 8 -- and a median of codes means nothing, so they get the
    most common value too, as does any column the caller marks `as_category`
    (a column named like a code). Missing-value codes in `exclude` are not
    counted. None when the column has no usable values."""
    exclude = exclude or set()
    values = [v for r in rows if (v := str(r.get(column, "") or "").strip()) and v not in exclude]
    if not values:
        return None
    numbers = [parse_finite_float(v) for v in values]
    if all(n is not None for n in numbers) and not looks_like_codes(values) and not as_category:
        ordered = sorted(n for n in numbers if n is not None)
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        return "median", _format_decimal(Decimal(str(round(median, 4))))
    counts = Counter(values)
    top = max(counts.values())
    return "most common value", min(v for v, n in counts.items() if n == top)


def is_fill_candidate(column_type: str | None, filled: int, distinct: int) -> bool:
    """Whether "fill the gaps" makes sense for a column: numbers (filled
    with the median), or a real category -- one whose values repeat (at most
    half as many distinct values as filled cells). A column of mostly
    one-off values (notes, names, comments) gets no fill offer: its "most
    common value" would be an arbitrary sentence copied into every gap."""
    if column_type == "numeric":
        return filled > 0
    return column_type == "categorical" and filled > 0 and distinct <= max(2, filled // 2)


def blank_only_where(
    rows: list[dict], column: str, numeric_columns: list[str], exclude: dict[str, set[str]] | None = None
) -> tuple[str, float, float] | None:
    """(other column, low, high) when every blank in `column` falls in a
    range of another numeric column that no filled row reaches -- wages
    blank for exactly the ages 0-14, education for ages 0-2. Such blanks
    mean "not asked / not applicable", so filling them would invent answers
    (a toddler's wage). Needs 5+ rows on each side; None otherwise."""
    exclude = exclude or {}
    blank = [str(r.get(column) or "").strip() == "" for r in rows]
    if sum(blank) < 5:
        return None
    for other in numeric_columns:
        if other == column:
            continue
        codes = exclude.get(other, set())
        on_blank: list[float] = []
        on_filled: list[float] = []
        in_order: list[float] = []
        for r, is_blank in zip(rows, blank, strict=True):
            raw = str(r.get(other) or "").strip()
            value = None if raw in codes else parse_finite_float(raw)
            if value is not None:
                (on_blank if is_blank else on_filled).append(value)
                in_order.append(value)
        # A column that only rises (or falls) down the file -- a row number,
        # an enrolment sequence -- would make any run of trailing blanks look
        # "not applicable" (a measure that simply stopped being recorded).
        # (Only when it's also nearly all-unique, like an index: a file sorted
        # by age still has a real "not applicable" age range.)
        pairs = list(zip(in_order, in_order[1:]))
        index_like = len(set(in_order)) >= 0.9 * len(in_order)
        if index_like and (all(a <= b for a, b in pairs) or all(a >= b for a, b in pairs)):
            continue
        if len(on_blank) >= 5 and len(on_filled) >= 5 and (
            max(on_blank) < min(on_filled) or min(on_blank) > max(on_filled)
        ):
            return other, min(on_blank), max(on_blank)
    return None


def outlier_fix_params(outliers: dict, keep: list[str], action: str = "cap", *, whole_numbers: bool = False) -> dict:
    """The outliers-fix parameters from a detect_outliers result: the same
    IQR limits the check used (Q1 - 1.5 IQR, Q3 + 1.5 IQR). For a column of
    whole numbers (ages, counts) the limits are rounded outward to whole
    numbers, so a capped age is 113, not 112.5."""
    lower = outliers["q1"] - 1.5 * outliers["iqr"]
    upper = outliers["q3"] + 1.5 * outliers["iqr"]
    if whole_numbers:
        lower, upper = float(math.floor(lower)), float(math.ceil(upper))
    return {"action": action, "lower": round(lower, 4), "upper": round(upper, 4), "keep": sorted(keep)}


def check_judgment_fixes(rules: dict, fixes: dict, primary_key: list[str]) -> None:
    """Raise ValueError for judgment-call fixes that would undo themselves or
    damage the data, whichever way they arrive (the app or a rules file):

    - outliers or impute on a primary-key column (it would change record IDs);
    - an impute value that the approved missing-value or category mapping for
      that column maps away (re-cleaning would change it again), or that
      contains personal data the mask would remove.
    """
    for kind in ("outliers", "impute"):
        if touched := sorted(set(fixes.get(kind, {})) & set(primary_key)):
            raise ValueError(f"{kind} can't change the primary key column(s) {touched}")
    for col, value in fixes.get("impute", {}).items():
        mapped = set(rules.get("missing_values", {}).get(col, {})) | set(rules.get("category_mappings", {}).get(col, {}))
        if value in mapped or value.strip() in mapped:
            raise ValueError(f"impute value {value!r} for {col!r} is itself mapped away by an approved mapping")
        if fix_pii_mask(value) != value:
            raise ValueError(f"impute value for {col!r} contains personal data")


def mask_logged_personal_data(mutations: list[dict], columns: list[str] | set[str]) -> list[dict]:
    """For columns being masked, run every logged original and new value
    through the mask -- so an earlier change on the same column (a trimmed
    space, a mapping) can't write the personal data into the audit log."""
    columns = set(columns)
    for m in mutations:
        if m["column"] in columns:
            m["original_value"] = fix_pii_mask(str(m["original_value"]))
            m["new_value"] = fix_pii_mask(str(m["new_value"]))
    return mutations


def find_leading_zero_candidates(rows: list[dict], columns: list[str]) -> dict[str, dict]:
    """ZIP- and FIPS-named columns holding all-digit codes shorter than the
    standard width -- the usual result of a spreadsheet reading "08001" as
    the number 8001. ZIP width is 5; FIPS is 5 (county) when any value has
    4+ digits, else 2 (state). Returns {column: {"width", "count"}}."""
    zip_columns = set(find_zip_like_columns(columns))
    results: dict[str, dict] = {}
    for column in [c for c in columns if c in zip_columns or c in set(find_fips_like_columns(columns))]:
        digits = [v for v in (str(r.get(column, "")).strip() for r in rows) if v.isdigit()]
        if not digits:
            continue
        width = 5 if column in zip_columns or max(len(v) for v in digits) >= 4 else 2
        short = sum(1 for v in digits if len(v) < width)
        if short:
            results[column] = {"width": width, "count": short}
    return results


_UNIT_PARAMS = {"boundary", "minority_side", "factor", "minority_min", "minority_max"}


def unit_fix_params(finding: dict) -> dict:
    """The units-fix parameters for one detect_unit_inconsistency finding."""
    return {
        "boundary": finding["boundary"],
        "minority_side": finding["minority_side"],
        "factor": finding["expected_factor"],
        "minority_min": finding["minority_min"],
        "minority_max": finding["minority_max"],
    }


def validate_fixes(fixes: dict) -> None:
    """Raise ValueError for an unknown fix type or malformed parameters,
    so a bad rules file fails loudly instead of being half-applied."""
    unknown = set(fixes) - set(FIX_ORDER) - {"drop_exact_duplicate_rows"}
    if unknown:
        raise ValueError(f"unknown safe fix type(s): {sorted(unknown)}")
    for name in ("invisible", "whitespace", "mojibake", "numeric_format", "excel_dates", "pii_mask"):
        if name in fixes and not (
            isinstance(fixes[name], list) and all(isinstance(c, str) for c in fixes[name])
        ):
            raise ValueError(f"safe fix {name!r} must be a list of column names")
    for name in ("leading_zeros", "date_iso", "coded_units", "units", "outliers", "impute"):
        if name in fixes and not isinstance(fixes[name], dict):
            raise ValueError(f"safe fix {name!r} must be a mapping of column name -> setting, got {fixes[name]!r}")
    for col, width in fixes.get("leading_zeros", {}).items():
        if not isinstance(width, int) or isinstance(width, bool) or width < 1:
            raise ValueError(f"leading_zeros width for {col!r} must be a positive integer, got {width!r}")
    for col, fmt in fixes.get("date_iso", {}).items():
        if not isinstance(fmt, str) or "%" not in fmt:
            raise ValueError(f"date_iso format for {col!r} must be a strptime format string, got {fmt!r}")
    for col, rule in fixes.get("coded_units", {}).items():
        if not isinstance(rule, dict) or set(rule) != {"low", "high", "offset", "unit"}:
            raise ValueError(f"coded_units for {col!r} needs exactly high, low, offset, unit")
        if rule["unit"] not in ("kg", "cm"):
            raise ValueError(f"coded_units unit for {col!r} must be 'kg' or 'cm'")
        if any(isinstance(rule[k], bool) or not isinstance(rule[k], (int, float)) for k in ("low", "high", "offset")):
            raise ValueError(f"coded_units low, high and offset for {col!r} must be numbers")
        if rule["low"] >= rule["high"]:
            raise ValueError(f"coded_units low for {col!r} must be below high")
    for col, params in fixes.get("units", {}).items():
        if not isinstance(params, dict) or set(params) != _UNIT_PARAMS:
            raise ValueError(f"units fix for {col!r} needs exactly {', '.join(sorted(_UNIT_PARAMS))}")
        if params["minority_side"] not in ("low", "high"):
            raise ValueError(f"units minority_side for {col!r} must be 'low' or 'high'")
        if not isinstance(params["factor"], (int, float)) or params["factor"] <= 0:
            raise ValueError(f"units factor for {col!r} must be a positive number")
        for key in ("boundary", "minority_min", "minority_max"):
            if isinstance(params[key], bool) or not isinstance(params[key], (int, float)):
                raise ValueError(f"units {key} for {col!r} must be a number")
        if params["minority_min"] > params["minority_max"]:
            raise ValueError(f"units minority_min for {col!r} is greater than minority_max")
    for col, params in fixes.get("outliers", {}).items():
        if not isinstance(params, dict) or set(params) != {"action", "lower", "upper", "keep"}:
            raise ValueError(f"outliers fix for {col!r} needs exactly action, keep, lower, upper")
        if params["action"] not in ("cap", "blank"):
            raise ValueError(f"outliers action for {col!r} must be 'cap' or 'blank'")
        for key in ("lower", "upper"):
            if isinstance(params[key], bool) or not isinstance(params[key], (int, float)):
                raise ValueError(f"outliers {key} for {col!r} must be a number")
        if params["lower"] > params["upper"]:
            raise ValueError(f"outliers lower for {col!r} is greater than upper")
        if not (isinstance(params["keep"], list) and all(isinstance(v, str) for v in params["keep"])):
            raise ValueError(f"outliers keep for {col!r} must be a list of values")
    for col, fill in fixes.get("impute", {}).items():
        if not isinstance(fill, str) or not fill.strip():
            raise ValueError(f"impute value for {col!r} must be a non-empty string")
    if not isinstance(fixes.get("drop_exact_duplicate_rows", False), bool):
        raise ValueError("drop_exact_duplicate_rows must be true or false")


def _fixers(fixes: dict) -> list[tuple[str, str, Callable[[str], str]]]:
    """(fix name, column, one-argument function) in FIX_ORDER."""
    plan: list[tuple[str, str, Callable[[str], str]]] = []
    simple: dict[str, Callable[[str], str]] = {
        "invisible": fix_invisible,
        "whitespace": fix_whitespace,
        "mojibake": fix_mojibake,
        "numeric_format": fix_numeric_format,
        "excel_dates": fix_excel_date,
        "pii_mask": fix_pii_mask,
    }
    for name in FIX_ORDER:
        if name in simple:
            plan.extend((name, col, simple[name]) for col in fixes.get(name, []))
        elif name == "leading_zeros":
            plan.extend((name, col, partial(fix_leading_zeros, width=w)) for col, w in fixes.get(name, {}).items())
        elif name == "date_iso":
            plan.extend((name, col, partial(fix_date_iso, source_format=f)) for col, f in fixes.get(name, {}).items())
        elif name == "coded_units":
            plan.extend((name, col, partial(_convert_coded, rule=r, column=col)) for col, r in fixes.get(name, {}).items())
        elif name == "units":
            plan.extend((name, col, partial(fix_units, **p)) for col, p in fixes.get(name, {}).items())
        elif name == "outliers":
            plan.extend((name, col, partial(fix_outliers, **p)) for col, p in fixes.get(name, {}).items())
        elif name == "impute":
            # Called only on empty cells (see apply_safe_fixes): returns the fill.
            plan.extend((name, col, partial(_fill, value=v)) for col, v in fixes.get(name, {}).items())
    return plan


def _fill(_empty: str, *, value: str) -> str:
    return value


def _convert_coded(value: str, *, rule: dict, column: str) -> str:
    """A value the codebook records in another unit, in the column's own
    unit ("9088" -> "194"); anything else unchanged. The rule (range,
    offset, unit) rather than a list of values, so re-running the rules on
    a new release converts codes that release adds."""
    converted = convert_coded_unit(value, (rule["low"], rule["high"], rule["offset"], rule["unit"]), column)
    return value if converted is None else converted


def apply_safe_fixes(
    rows: list[dict], fixes: dict, primary_key: list[str], *, reason: str, row_numbers: bool = False
) -> tuple[list[dict], list[dict]]:
    """Apply approved value-level fixes. Returns (new rows, mutations).
    Row count and order are preserved (duplicate removal is separate --
    see drop_duplicate_rows). An empty cell is only ever touched by an
    approved "impute" fix, and that fix touches nothing else."""
    validate_fixes(fixes)
    check_judgment_fixes({}, fixes, primary_key)
    plan = _fixers(fixes)
    out_rows: list[dict] = []
    mutations: list[dict] = []
    for row_number, row in enumerate(rows, start=1):
        new_row = dict(row)
        row_key = {k: row.get(k) for k in primary_key}
        if row_numbers:  # see apply_transformations
            row_key["row"] = row_number
        for name, column, fixer in plan:
            original = new_row.get(column)
            empty = original is None or original == ""
            if empty != (name == "impute"):
                continue
            new_value = fixer("" if empty else str(original))
            if new_value != (original or ""):
                pii = is_pii_like_column(column)
                mutations.append(
                    {
                        "row_key": row_key,
                        "column": column,
                        # The masked text's original is the personal data itself.
                        "original_value": PII_MASK_PLACEHOLDER if pii or name == "pii_mask" else (original or ""),
                        "new_value": PII_MASK_PLACEHOLDER if pii else new_value,
                        "transformation_rule": f"{name}:{column}",
                        "reason": reason,
                    }
                )
                new_row[column] = new_value
        out_rows.append(new_row)
    return out_rows, mask_logged_personal_data(mutations, fixes.get("pii_mask", []))


def drop_duplicate_rows(
    rows: list[dict], duplicates: list[dict], primary_key: list[str], *, reason: str, row_numbers: bool = False
) -> tuple[list[dict], list[dict]]:
    """Remove the rows detect_duplicate_rows flagged (each `duplicates`
    entry has row_index and duplicate_of_row_index), keeping the first
    occurrence. Re-verifies that each removed row is still identical to
    the row it duplicates and raises ValueError if not -- removal is only
    ever applied to rows that are provably redundant."""
    to_remove: dict[int, int] = {}
    for d in duplicates:
        i, keep = d["row_index"], d["duplicate_of_row_index"]
        if not (0 <= i < len(rows) and 0 <= keep < len(rows)) or keep in to_remove:
            raise ValueError(f"invalid duplicate pair: row {i} -> row {keep}")
        if rows[i] != rows[keep]:
            raise ValueError(f"row {i + 1} is no longer identical to row {keep + 1}; refusing to remove it")
        to_remove[i] = keep
    mutations = [
        {
            "row_key": {k: rows[i].get(k) for k in primary_key} | ({"row": i + 1} if row_numbers else {}),
            "column": "(entire row)",
            "original_value": f"data row {i + 1}",
            "new_value": f"(removed: exact duplicate of data row {keep + 1})",
            "transformation_rule": "drop_exact_duplicate_row",
            "reason": reason,
        }
        for i, keep in sorted(to_remove.items())
    ]
    return [row for i, row in enumerate(rows) if i not in to_remove], mutations


def clean_dataset(
    rows: list[dict], rules: dict, fixes: dict, duplicates: list[dict], primary_key: list[str], *, reason: str
) -> dict:
    """The whole approved-cleaning sequence, in the one order every caller
    must use: missing-value and category mappings (their keys are the raw
    variants, which trimming first would orphan), then value-level safe
    fixes, then the 1:1 safety report, then -- only if that passed and it
    was approved -- exact-duplicate removal, the one change that alters
    the row count. `duplicates` are detect_duplicate_rows' findings on the
    raw `rows`; raw-identical rows stay identical through the mappings and
    fixes (all pure per-value functions), which drop_duplicate_rows
    re-verifies anyway.

    Raises ValueError for mappings the app and load_rules also refuse
    (a code/category conflict, or a chained mapping), so no caller can
    clean with a combination that isn't idempotent.

    Returns {"rows", "mutations", "safety", "rows_removed"}. When
    `safety["all_passed"]` is False the rows must not be offered as output.
    """
    for column in set(rules.get("missing_values", {})) & set(rules.get("category_mappings", {})):
        conflicts = find_mapping_conflicts(rules["missing_values"][column], rules["category_mappings"][column])
        if conflicts:
            raise ValueError(f"column {column!r}: missing-value and category mappings conflict on {conflicts}")
    for kind in ("missing_values", "category_mappings"):
        for column, mapping in rules.get(kind, {}).items():
            if chained := find_chained_keys(mapping):
                raise ValueError(f"column {column!r}: {kind} would chain on {chained}")
    validate_fixes({k: v for k, v in fixes.items()})
    check_judgment_fixes(rules, fixes, primary_key)
    # A key that repeats can't say which row a change was made in, so the
    # log then also names the data row.
    row_numbers = len({tuple(r.get(k) for k in primary_key) for r in rows}) < len(rows)
    out, mutations = apply_transformations(rows, rules, reason=reason, row_numbers=row_numbers)
    mask_logged_personal_data(mutations, fixes.get("pii_mask", []))
    value_fixes = {k: v for k, v in fixes.items() if k != "drop_exact_duplicate_rows"}
    out, fix_mutations = apply_safe_fixes(out, value_fixes, primary_key, reason=reason, row_numbers=row_numbers)
    mutations += fix_mutations
    safety = compute_safety_report(rows, out, primary_key)
    removed: list[dict] = []
    if safety["all_passed"] and fixes.get("drop_exact_duplicate_rows"):
        out, removed = drop_duplicate_rows(out, duplicates, primary_key, reason=reason, row_numbers=row_numbers)
        mutations += removed
    return {"rows": out, "mutations": mutations, "safety": safety, "rows_removed": len(removed)}
