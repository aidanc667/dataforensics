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
    for rule_name in ("missing_values", "category_mappings"):
        if not isinstance(raw[rule_name], dict):
            raise RulesConfigError(
                f"Rules file {path}: '{rule_name}' must be a YAML mapping of column name -> "
                f"rule (got {raw[rule_name]!r}) — if you don't need any {rule_name} rules, "
                "omit the key entirely rather than leaving it empty"
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
