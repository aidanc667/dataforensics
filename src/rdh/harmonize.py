from rdh.typing_guards import classify_sentinel, is_pii_like_column

# Same placeholder used by validation.py's _display_value and dictionary.py's
# _PII_MASK_MESSAGE, kept identical across all three masking sites for
# consistency. This is a naming-convention heuristic, not a guarantee about
# the column's actual contents, so we never claim "PII-safe" here either.
_PII_MASK_PLACEHOLDER = "[masked: potential identifier pattern detected]"


class HarmonizeSafetyError(Exception):
    """Raised by assert_row_and_column_integrity when the harmonize
    pipeline's row-count / column-count safety net fails.

    This is the explicit backstop for the spec invariant "rows are never
    silently deleted and columns are never silently dropped ... asserted
    after every run unless a rule explicitly removed something." No rule
    type in this codebase currently declares a column removal, so the
    column check is unconditional wherever it applies. It exists
    independently of any single upstream fix (e.g. duplicate-header
    detection in ingest.py) so that a future regression anywhere in the
    transform pipeline is still caught here, before anything is written to
    disk."""


def assert_row_and_column_integrity(
    input_rows: list[dict],
    output_rows: list[dict],
    *,
    context: str,
    columns: str = "exact",
) -> None:
    """Assert output_rows has exactly as many rows as input_rows, and
    (depending on ``columns``) the same column structure.

    ``columns``:
      - "exact": output column *names* must exactly match input column names.
        Correct for apply_transformations, which only substitutes values and
        never renames/adds/removes columns.
      - "count": only the output column *count* must match the input column
        count -- names may legitimately change. Correct for apply_crosswalk,
        whose column_map is the one rule type in this codebase that
        deliberately renames columns; but two distinct source columns
        silently colliding onto the same target name would still be a
        silent column loss, so the count is still checked.
      - "skip": no column check (e.g. when input_rows is empty and there is
        nothing meaningful to compare).
    """
    if len(output_rows) != len(input_rows):
        raise HarmonizeSafetyError(
            f"{context}: {len(input_rows)} input row(s) became {len(output_rows)} output "
            "row(s) -- rows must never be silently added or dropped"
        )

    if columns == "skip" or not input_rows:
        return

    input_columns = list(input_rows[0].keys())
    output_columns = list(output_rows[0].keys()) if output_rows else []

    if columns == "exact":
        if set(input_columns) != set(output_columns):
            raise HarmonizeSafetyError(
                f"{context}: input columns {sorted(set(input_columns))} do not match "
                f"output columns {sorted(set(output_columns))} -- columns must never be "
                "silently dropped or added"
            )
    elif columns == "count":
        if len(output_columns) != len(input_columns):
            raise HarmonizeSafetyError(
                f"{context}: {len(input_columns)} input column(s) became "
                f"{len(output_columns)} output column(s) -- columns must never be "
                "silently dropped or merged"
            )
    else:
        raise ValueError(f"unknown columns mode: {columns!r}")


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
        # Known limitation: row_key is not masked even if a primary_key column
        # is itself PII-like (e.g. primary_key: [ssn]). Masking it would break
        # the manifest's ability to reference which row a mutation belongs to,
        # so this is a deliberate scope boundary, not an oversight — avoid
        # choosing a PII-pattern column as a primary key if this matters.
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
