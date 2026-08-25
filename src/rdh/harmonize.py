from rdh.typing_guards import classify_sentinel


def plan_transformations(rows: list[dict], rules: dict) -> list[dict]:
    plan = []
    missing_values = rules.get("missing_values", {})

    for column, sentinel_map in missing_values.items():
        affected = sum(
            1 for row in rows if classify_sentinel(row.get(column), sentinel_map) is not None
        )
        if affected:
            plan.append(
                {
                    "rule": f"missing_value_sentinel:{column}",
                    "column": column,
                    "rows_affected": affected,
                }
            )

    category_mappings = rules.get("category_mappings", {})
    for column, mapping in category_mappings.items():
        affected = sum(1 for row in rows if row.get(column) in mapping)
        if affected:
            plan.append(
                {
                    "rule": f"category_mapping:{column}",
                    "column": column,
                    "rows_affected": affected,
                }
            )

    return plan
