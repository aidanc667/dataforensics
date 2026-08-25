from pathlib import Path

import yaml


class RulesConfigError(Exception):
    pass


_REQUIRED_KEYS = ("version", "primary_key", "columns")


def load_rules(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise RulesConfigError(f"Malformed YAML in {path}: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesConfigError(f"Could not read rules file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RulesConfigError(f"Rules file {path} must be a YAML mapping at the top level")

    for key in _REQUIRED_KEYS:
        if key not in raw:
            raise RulesConfigError(f"Rules file {path} is missing required key: {key}")

    if not isinstance(raw["primary_key"], list) or not raw["primary_key"]:
        raise RulesConfigError(f"Rules file {path}: primary_key must be a non-empty list")

    raw.setdefault("missing_values", {})
    raw.setdefault("category_mappings", {})
    raw.setdefault("weights_strata", {"columns": []})

    # A key present in the YAML with no entries under it (e.g.
    # "category_mappings:" with nothing indented beneath) parses to `None`,
    # not `{}` -- setdefault above does NOT replace it, since the key
    # already exists. Likewise a bare scalar (e.g. "missing_values: 5") is
    # valid YAML but not a mapping. Both would otherwise crash later with an
    # uncaught TypeError (e.g. `set(None)`, `None.items()`) instead of a
    # clean, actionable RulesConfigError -- catch it here, immediately after
    # the defaults are applied and before anything downstream assumes a
    # dict.
    #
    # `columns` is a REQUIRED key (see _REQUIRED_KEYS above), so unlike
    # missing_values/category_mappings there is no setdefault to fall back
    # on -- but it has the exact same None/scalar failure mode ("columns:"
    # with nothing indented beneath it parses to `None`; "columns: 5" is a
    # bare scalar), and validation.py's `rules.get("columns", {})` /
    # `columns_rules.items()` would otherwise crash with the same uncaught
    # TypeError class. Validate it here too.
    for rule_name in ("missing_values", "category_mappings", "columns"):
        if not isinstance(raw[rule_name], dict):
            if rule_name == "columns":
                # Unlike missing_values/category_mappings, `columns` is a
                # REQUIRED key (see _REQUIRED_KEYS above) -- "omit the key
                # entirely" is actively wrong advice here, since omitting it
                # produces a *different* error ("missing required key:
                # columns"), not a fix. Point at the actual correct fix
                # instead: an explicit empty mapping.
                raise RulesConfigError(
                    f"Rules file {path}: '{rule_name}' must be a YAML mapping of column name -> "
                    f"rule (got {raw[rule_name]!r}) — use `columns: {{}}` if you have no "
                    "column-level rules, since `columns` is a required key and cannot be omitted"
                )
            raise RulesConfigError(
                f"Rules file {path}: '{rule_name}' must be a YAML mapping of column name -> "
                f"rule (got {raw[rule_name]!r}) — if you don't need any {rule_name} rules, "
                "omit the key entirely rather than leaving it empty"
            )

    # Each column's own rule-set under `columns` must itself be a dict (e.g.
    # "columns:\n  age: 5" is a bare scalar instead of a rule-set), and any
    # `minimum`/`maximum` bound within it must be numeric -- validation.py's
    # `col_rules["minimum"]` / `numeric < col_rules["minimum"]` comparisons
    # would otherwise crash with an uncaught TypeError (or silently compare
    # against a non-numeric value) instead of failing cleanly here. Likewise
    # `format`, if present, must be a string -- validation.py passes it
    # straight into `datetime.strptime(raw_value, declared_format)`, which
    # raises an uncaught TypeError (not a clean, catchable ValueError) when
    # ``declared_format`` isn't a string (e.g. "format: 5").
    for column, col_rules in raw["columns"].items():
        if not isinstance(col_rules, dict):
            raise RulesConfigError(
                f"Rules file {path}: columns['{column}'] must be a YAML mapping of rule "
                f"name -> value (got {col_rules!r})"
            )
        for bound_key in ("minimum", "maximum"):
            if bound_key in col_rules and not isinstance(col_rules[bound_key], (int, float)):
                raise RulesConfigError(
                    f"Rules file {path}: columns['{column}']['{bound_key}'] must be numeric "
                    f"(got {col_rules[bound_key]!r})"
                )
        if "format" in col_rules and not isinstance(col_rules["format"], str):
            raise RulesConfigError(
                f"Rules file {path}: columns['{column}']['format'] must be a string "
                f"(got {col_rules['format']!r})"
            )

    overlap = sorted(set(raw["missing_values"]) & set(raw["category_mappings"]))
    if overlap:
        cols = ", ".join(f"'{col}'" for col in overlap)
        raise RulesConfigError(
            f"Column(s) {cols} appear in both missing_values and category_mappings — "
            "this ordering is ambiguous and not supported; use only one rule type per column"
        )

    # A mapping entry (category_mappings OR missing_values -- both apply a
    # single key->value substitution per row) whose targets (values) overlap
    # with its own sources (keys) chains on re-application: e.g.
    # {M: Male, Male: Female} turns "M" -> "Male" on the first run, then
    # "Male" -> "Female" on a second run over that same output; likewise
    # {"99": "Refused", "Refused": "Unknown"} in missing_values. This
    # violates "running harmonize twice on the same output must not change
    # it further" (idempotency), so it's rejected up front here, the same
    # way the missing_values/category_mappings column overlap above is.
    #
    # A *self*-mapping (key == value, e.g. {M: Male, Male: Male}) is a
    # common, safe, idempotent defensive pattern -- map the short code, and
    # leave the already-canonical value as a no-op. It must not be flagged:
    # only an actual value-to-key collision among non-identity mappings is a
    # real chain.
    for rule_name in ("category_mappings", "missing_values"):
        for column, mapping in raw[rule_name].items():
            if not isinstance(mapping, dict):
                # e.g. "category_mappings:\n  sex: [M, F]" -- a list (or any
                # other non-mapping) instead of a column -> mapping dict.
                # Silently skipping this (the old `continue` behavior) left
                # the malformed value in place, so it would go on to crash
                # later in apply_transformations/plan_transformations with
                # an uncaught TypeError (e.g. list indices must be
                # integers, not str) instead of failing cleanly here.
                raise RulesConfigError(
                    f"Rules file {path}: {rule_name} for column '{column}' must be a YAML "
                    f"mapping (key -> value), got {mapping!r}"
                )

            for key, value in mapping.items():
                try:
                    hash(value)
                except TypeError:
                    raise RulesConfigError(
                        f"Rules file {path}: {rule_name} for column '{column}' has a "
                        f"non-hashable mapping target for key '{key}' ({value!r}) -- mapping "
                        "targets must be simple scalar values (e.g. a string), not a list/dict"
                    ) from None

            # Exclude self-mappings (key == value) from BOTH sides before
            # intersecting: a self-mapped key is a no-op on re-application,
            # so it can neither participate in a chain as a source nor
            # count as a colliding target. {M: Male, Male: Male} must NOT
            # be flagged (re-applying is a no-op: "Male" maps to itself),
            # even though "Male" is literally both a value and a key in the
            # raw mapping -- only an actual value-to-key collision among
            # non-identity entries is a real chain.
            non_identity = {key: value for key, value in mapping.items() if value != key}
            chained = sorted(set(non_identity.values()) & set(non_identity.keys()))
            if chained:
                targets = ", ".join(f"'{c}'" for c in chained)
                raise RulesConfigError(
                    f"{rule_name} for column '{column}' chains: {targets} appear as both "
                    "a mapping target and a mapping source — this breaks idempotency (re-running "
                    "harmonize on already-harmonized output would keep changing values); use "
                    "non-overlapping source/target values"
                )

    return raw
