import re
from datetime import datetime

from rdh import dictionary

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def is_ambiguous_date(value: str) -> bool:
    if _ISO_DATE_PATTERN.match(value):
        return False
    return bool(_SLASH_DATE_PATTERN.match(value))


def _row_key(row: dict, primary_key: list[str]) -> dict:
    return {k: row.get(k) for k in primary_key}


def validate(rows: list[dict], rules: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    suggestions: list[dict] = []
    checks_evaluated = 0

    primary_key = rules["primary_key"]
    columns_rules = rules.get("columns", {})

    seen_keys: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row.get(k) for k in primary_key)
        checks_evaluated += 1
        if key in seen_keys:
            errors.append(
                {
                    "column": ",".join(primary_key),
                    "row_key": _row_key(row, primary_key),
                    "rule": "duplicate_primary_key",
                    "message": f"Duplicate primary key value: {key}",
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
                            "message": f"{column}={raw_value} is below configured minimum {col_rules['minimum']}",
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
                            "message": f"{column}={raw_value} is above configured maximum {col_rules['maximum']} — may still be valid",
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
                                "message": f"{column}={raw_value} does not match declared format {declared_format}",
                                "severity": "error",
                            }
                        )
                elif is_ambiguous_date(raw_value):
                    errors.append(
                        {
                            "column": column,
                            "row_key": row_key,
                            "rule": "ambiguous_date_format",
                            "message": f"{column}={raw_value} is ambiguous (MM/DD vs DD/MM) with no declared format — not parsed",
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
        # detection in dictionary.py.
        numeric_values: list[float] = []
        is_numeric = True
        for raw_value, _row_key_ in values_with_keys:
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
                        "message": f"{column}={raw_value} is a statistical outlier (IQR method) — not necessarily incorrect",
                        "severity": "suggestion",
                    }
                )

        # 2. Rare category suggestions — only run on columns that look
        # categorical-ish (unique-value count < half the non-null row
        # count), so ID-shaped/free-text columns aren't flagged wholesale.
        value_counts: dict[str, int] = {}
        for raw_value, _row_key_ in values_with_keys:
            value_counts[raw_value] = value_counts.get(raw_value, 0) + 1

        unique_count = len(value_counts)
        non_null_count = len(values_with_keys)
        looks_categorical = unique_count > 1 and unique_count < (non_null_count / 2)

        if looks_categorical:
            for raw_value, row_key in values_with_keys:
                if value_counts[raw_value] == 1:
                    suggestions.append(
                        {
                            "column": column,
                            "row_key": row_key,
                            "rule": "rare_category",
                            "message": f"{column}={raw_value} occurs only once in this dataset — may be valid, not necessarily an error",
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
