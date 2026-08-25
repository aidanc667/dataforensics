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
                continue

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
