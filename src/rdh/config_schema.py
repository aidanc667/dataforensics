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

    # A category_mappings entry whose mapping targets (values) overlap with
    # its own mapping sources (keys) chains on re-application: e.g.
    # {M: Male, Male: Female} turns "M" -> "Male" on the first run, then
    # "Male" -> "Female" on a second run over that same output. This
    # violates "running harmonize twice on the same output must not change
    # it further" (idempotency), so it's rejected up front here, the same
    # way the missing_values/category_mappings column overlap above is.
    for column, mapping in raw["category_mappings"].items():
        if not isinstance(mapping, dict):
            continue
        chained = sorted(set(mapping.values()) & set(mapping.keys()))
        if chained:
            targets = ", ".join(f"'{c}'" for c in chained)
            raise RulesConfigError(
                f"category_mappings for column '{column}' chains: {targets} appear as both "
                "a mapping target and a mapping source — this breaks idempotency (re-running "
                "harmonize on already-harmonized output would keep changing values); use "
                "non-overlapping source/target values"
            )

    return raw
