from rdh.typing_guards import classify_sentinel, is_pii_like_column

# Same placeholder used by validation.py's _display_value and dictionary.py's
# _PII_MASK_MESSAGE, kept identical across all three masking sites for
# consistency. This is a naming-convention heuristic, not a guarantee about
# the column's actual contents, so we never claim "PII-safe" here either.
_PII_MASK_PLACEHOLDER = "[masked: potential identifier pattern]"


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


def apply_transformations(rows: list[dict], rules: dict) -> tuple[list[dict], list[dict]]:
    primary_key = rules["primary_key"]
    missing_values = rules.get("missing_values", {})
    category_mappings = rules.get("category_mappings", {})

    transformed = []
    mutations = []

    for row in rows:
        new_row = dict(row)
        row_key = {k: row.get(k) for k in primary_key}

        for column, sentinel_map in missing_values.items():
            original = new_row.get(column)
            new_value = classify_sentinel(original, sentinel_map)
            if new_value is not None:
                pii = is_pii_like_column(column)
                mutations.append(
                    {
                        "row_key": row_key,
                        "column": column,
                        "original_value": _PII_MASK_PLACEHOLDER if pii else original,
                        "new_value": _PII_MASK_PLACEHOLDER if pii else new_value,
                        "transformation_rule": f"missing_value_sentinel:{column}",
                    }
                )
                new_row[column] = new_value

        for column, mapping in category_mappings.items():
            original = new_row.get(column)
            if original in mapping:
                new_value = mapping[original]
                pii = is_pii_like_column(column)
                mutations.append(
                    {
                        "row_key": row_key,
                        "column": column,
                        "original_value": _PII_MASK_PLACEHOLDER if pii else original,
                        "new_value": _PII_MASK_PLACEHOLDER if pii else new_value,
                        "transformation_rule": f"category_mapping:{column}",
                    }
                )
                new_row[column] = new_value

        transformed.append(new_row)

    return transformed, mutations


def apply_crosswalk(rows: list[dict], source_crosswalk: dict) -> list[dict]:
    """Remap a single source's rows onto a shared target schema.

    Renames columns per ``column_map`` and, where a renamed column also has
    an entry in ``value_map``, translates matching values (e.g. numeric PUMS
    sex codes -> "M"/"F"). Operates on one source's rows at a time and never
    combines rows across sources -- the caller is responsible for writing
    each source's remapped rows to its own output file.
    """
    column_map = source_crosswalk.get("column_map", {})
    value_map = source_crosswalk.get("value_map", {})

    remapped = []
    for row in rows:
        new_row = {}
        for old_col, value in row.items():
            new_col = column_map.get(old_col, old_col)
            if new_col in value_map and value in value_map[new_col]:
                value = value_map[new_col][value]
            new_row[new_col] = value
        remapped.append(new_row)
    return remapped
