import re
from datetime import datetime

from dataforensics import dictionary
from dataforensics.typing_guards import is_id_like_column, is_pii_like_column, preserves_leading_zero

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_DASH_DATE_PATTERN = re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$")

# Same honest phrasing as dictionary.py's _PII_MASK_MESSAGE: this is a
# naming-convention heuristic, not a guarantee about the column's actual
# contents, so we never claim "PII-safe" or "HIPAA-compliant" here either.
_PII_MASK_PLACEHOLDER = "[masked: potential identifier pattern detected]"


def is_ambiguous_date(value: str) -> bool:
    if _ISO_DATE_PATTERN.match(value):
        return False
    return bool(_SLASH_DATE_PATTERN.match(value) or _DASH_DATE_PATTERN.match(value))


def _row_key(row: dict, primary_key: list[str]) -> dict:
    # Known limitation: not masked even if a primary_key column is itself
    # PII-like (e.g. primary_key: [ssn]) — masking it would break the
    # report's ability to reference which row a finding belongs to. This is
    # a deliberate scope boundary, matching harmonize.py's identical choice;
    # avoid choosing a PII-pattern column as a primary key if this matters.
    return {k: row.get(k) for k in primary_key}


def _display_value(column: str, raw_value) -> str:
    """Return raw_value as-is, unless `column` matches a PII-like naming
    pattern — in which case return a masked placeholder instead. Finding
    messages must never embed a raw cell value from a PII-like column,
    since those messages get written verbatim into generated Markdown/JSON
    reports (the same safety requirement dictionary.py already enforces for
    levels/sample values)."""
    if is_pii_like_column(column):
        return _PII_MASK_PLACEHOLDER
    return raw_value


def validate(rows: list[dict], rules: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    suggestions: list[dict] = []
    checks_evaluated = 0

    primary_key = rules["primary_key"]
    columns_rules = rules.get("columns", {})

    # If any primary-key component column looks PII-like, the key value
    # itself (a tuple of raw cell values) must not be embedded raw in the
    # finding message either.
    pk_is_pii = any(is_pii_like_column(k) for k in primary_key)

    seen_keys: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row.get(k) for k in primary_key)
        checks_evaluated += 1
        if key in seen_keys:
            key_display = _PII_MASK_PLACEHOLDER if pk_is_pii else key
            errors.append(
                {
                    "column": ",".join(primary_key),
                    "row_key": _row_key(row, primary_key),
                    "rule": "duplicate_primary_key",
                    "message": f"Duplicate primary key value: {key_display}",
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
                            "message": f"{column}={_display_value(column, raw_value)} is below configured minimum {col_rules['minimum']}",
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
                            "message": f"{column}={_display_value(column, raw_value)} is above configured maximum {col_rules['maximum']} — may still be valid",
                            "severity": "warning",
                        }
                    )

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
                                "message": f"{column}={_display_value(column, raw_value)} does not match declared format {declared_format}",
                                "severity": "error",
                            }
                        )
                elif is_ambiguous_date(raw_value):
                    errors.append(
                        {
                            "column": column,
                            "row_key": row_key,
                            "rule": "ambiguous_date_format",
                            "message": f"{column}={_display_value(column, raw_value)} is ambiguous (MM/DD vs DD/MM) with no declared format — not parsed",
                            "severity": "error",
                        }
                    )

    # Suggestion-tier heuristic checks: outliers (IQR) and rare categories.
    # These run for every column present in the data, independent of
    # `columns_rules` — suggestions are heuristic and never promote to
    # error/warning tier, so they never affect checks_passed.
    all_columns: list[str] = []
    seen_columns: set[str] = set()
    for row in rows:
        for column in row:
            if column not in seen_columns:
                seen_columns.add(column)
                all_columns.append(column)

    for column in all_columns:
        values_with_keys: list[tuple[str, dict]] = []
        for row in rows:
            raw_value = row.get(column)
            if raw_value in (None, ""):
                continue
            values_with_keys.append((raw_value, _row_key(row, primary_key)))

        if not values_with_keys:
            continue

        # 1. Outlier suggestions (IQR method) — only if the entire column
        # parses as numeric, mirroring build_data_dictionary's numeric
        # detection in dictionary.py. dictionary.py skips numeric parsing
        # (and therefore outlier-testing) for a column when
        # `category != "id" and not mask_pii` is false -- i.e. it skips
        # BOTH ID-like columns AND PII-like columns. "id" is assigned when
        # EITHER is_id_like_column(name) matches OR
        # preserves_leading_zero(non_null_values) is true — e.g. a column
        # named "county_code" doesn't match the ID-name pattern, but its
        # leading-zero values ("06081") must still never be float()-cast and
        # IQR-tested, or the leading zero would be destroyed. And a
        # PII-like column (e.g. "phone") must never be numerically parsed
        # either, even though its message is separately masked via
        # _display_value — firing a suggestion at all on a PII column
        # would still contradict dictionary.py's treatment of the same
        # column. All three conditions must be checked here to actually
        # mirror dictionary.py's logic, token-for-token.
        column_values = [v for v, _ in values_with_keys]
        is_id_or_pii_like = (
            is_id_like_column(column)
            or preserves_leading_zero(column_values)
            or is_pii_like_column(column)
        )
        is_numeric = not is_id_or_pii_like
        numeric_values: list[float] = []
        for raw_value, _row_key_ in values_with_keys:
            if not is_numeric:
                break
            try:
                numeric_values.append(float(raw_value))
            except ValueError:
                is_numeric = False
                break

        if is_numeric:
            outliers = dictionary.detect_outliers(numeric_values)
            for idx in outliers["outlier_indices"]:
                raw_value, row_key = values_with_keys[idx]
                suggestions.append(
                    {
                        "column": column,
                        "row_key": row_key,
                        "rule": "iqr_outlier",
                        "message": f"{column}={_display_value(column, raw_value)} is a statistical outlier (IQR method) — not necessarily incorrect",
                        "severity": "suggestion",
                    }
                )

        # 2. Rare category suggestions — only run on columns that look
        # categorical-ish (unique-value count < half the non-null row
        # count), so free-text columns aren't flagged wholesale. The
        # cardinality heuristic alone isn't enough to exclude ID-shaped or
        # PII-like columns, though: a *low-cardinality* ID-shaped column
        # (e.g. county_fips with only 2 distinct leading-zero values across
        # many rows) can still satisfy "unique_count < half the row count"
        # and get flagged as a rare category despite dictionary.py
        # classifying it category "id" — and a low-cardinality PII-like
        # column (e.g. a "phone" column with a handful of shared area
        # codes) would likewise get a suggestion fired on it even though
        # its message is masked, contradicting dictionary.py's treatment of
        # the same column — so apply the same combined ID/PII guard used
        # for outlier suggestions above.
        value_counts: dict[str, int] = {}
        for raw_value, _row_key_ in values_with_keys:
            value_counts[raw_value] = value_counts.get(raw_value, 0) + 1

        unique_count = len(value_counts)
        non_null_count = len(values_with_keys)
        looks_categorical = (
            not is_id_or_pii_like and unique_count > 1 and unique_count < (non_null_count / 2)
        )

        if looks_categorical:
            for raw_value, row_key in values_with_keys:
                if value_counts[raw_value] == 1:
                    suggestions.append(
                        {
                            "column": column,
                            "row_key": row_key,
                            "rule": "rare_category",
                            "message": f"{column}={_display_value(column, raw_value)} occurs only once in this dataset — may be valid, not necessarily an error",
                            "severity": "suggestion",
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
