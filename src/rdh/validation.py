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

    checks_passed = checks_evaluated - len(errors) - len(warnings)
    return {
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "checks_evaluated": checks_evaluated,
        "checks_passed": checks_passed,
    }
