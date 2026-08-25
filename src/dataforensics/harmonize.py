from dataforensics.typing_guards import classify_sentinel, is_pii_like_column

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
    column check is unconditional wherever it applies. When callers pass
    ``input_columns`` AND ``input_row_count`` derived independently from
    the on-disk file (see ``assert_row_and_column_integrity``'s docstring
    -- cli.py does this via ``cli._read_header_and_row_count``), both the
    column check and the row check are independent of a regression confined
    to ``dictionary.py``'s own parse *composition* -- e.g. a duplicate-header
    dict-collapse, or a mis-slice specific to how ``dictionary.py`` drives
    ``ingest.strip_footer`` -- because cli.py's anchor is a separate call
    path that never shares that composition. This independence does NOT
    extend to a regression inside a primitive both paths call directly:
    cli.py's anchor and ``dictionary.py``'s parse both call the very same
    ``ingest.strip_footer`` (and ``detect_delimiter`` / ``detect_encoding``)
    function -- two bindings of one function, not two independent
    implementations -- so a bug inside ``strip_footer`` itself (e.g. its
    field-count heuristic misclassifying and dropping a genuine data line)
    corrupts both sides identically and would NOT be caught here. If a
    caller omits ``input_row_count`` (or ``input_columns``), that half of
    the check falls back to comparing against ``input_rows`` itself and
    loses even the composition-level independence -- see the parameter docs
    below."""


def column_union(rows: list[dict]) -> list[str]:
    """Union of keys across every row, preserving first-seen order.

    Checking only rows[0] misses per-row column drift -- e.g. one row
    carries an extra '' key from a trailing-delimiter line while another
    doesn't. That kind of drift would otherwise pass this check on row 0 and
    only surface later as an uncaught csv.DictWriter ValueError, instead of
    being caught cleanly here.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def assert_row_and_column_integrity(
    input_rows: list[dict],
    output_rows: list[dict],
    *,
    context: str,
    columns: str = "exact",
    input_columns: list[str] | None = None,
    input_row_count: int | None = None,
) -> None:
    """Assert output_rows has exactly as many rows as input_rows, and
    (depending on ``columns``) the same column structure.

    ``input_columns``, when given, overrides the column set derived from
    ``input_rows`` for the column-structure check. ``input_row_count``,
    when given, overrides the row count derived from ``input_rows`` for the
    row-count check, the same way ``input_columns`` overrides it for the
    column check. Callers anchoring this check to the actual on-disk file
    (e.g. cli.py, via ``cli._read_header_and_row_count``) should pass
    both explicitly -- otherwise this check only compares two views that
    were both already derived from the same upstream parse (e.g.
    dictionary.read_rows), so a bug in that parse's *composition* (such as
    a duplicate-header dict-collapse, or a mis-slice specific to how
    dictionary.py drives ``strip_footer``) would corrupt both sides
    identically and this check would pass trivially on already-corrupted
    data. Passing the independently re-derived file header and row count
    closes that composition-level gap for both checks -- but it does NOT
    close a gap caused by a regression inside a primitive that both the
    anchor and the parse call directly (``ingest.strip_footer``,
    ``detect_delimiter``, ``detect_encoding``): those are shared bindings
    of the same function, not independent implementations, so a bug inside
    e.g. ``strip_footer`` itself would still corrupt the anchor and the
    parse identically and would NOT be caught by this check.

    ``columns``:
      - "exact": output column *names* must exactly match input column names
        (as a multiset, not just as a set -- so a dropped duplicate name,
        e.g. header ['pid', 'sex', 'sex'] collapsing to ['pid', 'sex'],
        still trips this even though the *set* of names is unchanged).
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
    expected_row_count = len(input_rows) if input_row_count is None else input_row_count
    if len(output_rows) != expected_row_count:
        raise HarmonizeSafetyError(
            f"{context}: {expected_row_count} input row(s) became {len(output_rows)} output "
            "row(s) -- rows must never be silently added or dropped"
        )

    if columns == "skip" or not input_rows:
        return

    resolved_input_columns = list(input_columns) if input_columns is not None else column_union(input_rows)
    output_columns = column_union(output_rows)

    if columns == "exact":
        if len(resolved_input_columns) != len(output_columns) or set(resolved_input_columns) != set(
            output_columns
        ):
            raise HarmonizeSafetyError(
                f"{context}: input columns {sorted(resolved_input_columns)} "
                f"(count {len(resolved_input_columns)}) do not match output columns "
                f"{sorted(output_columns)} (count {len(output_columns)}) -- columns must "
                "never be silently dropped or added"
            )
    elif columns == "count":
        if len(output_columns) != len(resolved_input_columns):
            raise HarmonizeSafetyError(
                f"{context}: {len(resolved_input_columns)} input column(s) became "
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


def apply_transformations(
    rows: list[dict], rules: dict, *, reason: str = "Specified in the rules file"
) -> tuple[list[dict], list[dict]]:
    """Apply `rules`'s missing_values/category_mappings to `rows`.

    `reason` is recorded verbatim on every mutation this call produces --
    it's the human-facing "why" in the provenance log (e.g. "Specified in
    the rules file" for the CLI's --rules path, or "Approved by user during
    interactive review" for an app that assembled rules from clicked
    approvals). It does not affect behavior, only the audit trail.
    """
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
                        "reason": reason,
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
                        "reason": reason,
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
