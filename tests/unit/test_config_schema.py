import pytest

from rdh.config_schema import RulesConfigError, load_rules


def test_load_valid_rules(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns:\n"
        "  age:\n"
        "    type: integer\n"
        "    minimum: 0\n"
        "    maximum: 120\n"
    )
    rules = load_rules(f)
    assert rules["version"] == 1
    assert rules["primary_key"] == ["participant_id"]
    assert rules["columns"]["age"]["minimum"] == 0


def test_load_rules_missing_version_fails(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("primary_key: [id]\ncolumns: {}\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_missing_primary_key_fails(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\ncolumns: {}\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_malformed_yaml_fails(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\n  bad indent: [\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_defaults_missing_optional_sections(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns: {}\n")
    rules = load_rules(f)
    assert rules["missing_values"] == {}
    assert rules["category_mappings"] == {}
    assert rules["weights_strata"] == {"columns": []}


def test_load_rules_rejects_column_in_both_missing_values_and_category_mappings(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  sex:\n"
        "    \"9\": Unknown\n"
        "category_mappings:\n"
        "  sex:\n"
        "    M: Male\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_chained_category_mappings(tmp_path):
    # {M: Male, Male: Female} chains: applying once turns "M" -> "Male";
    # applying the same rules again over that output would turn "Male" ->
    # "Female", violating "running harmonize twice on the same output must
    # not change it further."
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "category_mappings:\n"
        "  sex:\n"
        "    M: Male\n"
        "    Male: Female\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_accepts_non_chained_category_mappings(tmp_path):
    # Sanity check that the chained-mapping rejection doesn't over-fire on
    # an ordinary, non-overlapping category mapping.
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "category_mappings:\n"
        "  sex:\n"
        "    M: Male\n"
        "    F: Female\n"
    )
    rules = load_rules(f)
    assert rules["category_mappings"]["sex"] == {"M": "Male", "F": "Female"}


def test_load_rules_accepts_idempotent_self_mapping(tmp_path):
    # {M: Male, Male: Male} is a common, safe defensive pattern -- map the
    # short code, and leave the already-canonical value as a no-op
    # self-mapping. It is already idempotent (running it twice changes
    # nothing further) and must NOT be rejected as "chained".
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "category_mappings:\n"
        "  sex:\n"
        "    M: Male\n"
        "    Male: Male\n"
    )
    rules = load_rules(f)
    assert rules["category_mappings"]["sex"] == {"M": "Male", "Male": "Male"}


def test_load_rules_accepts_bare_self_mapping(tmp_path):
    # A bare {Male: Male} (no short-code entry at all) is likewise idempotent
    # and must not be rejected.
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "category_mappings:\n"
        "  sex:\n"
        "    Male: Male\n"
    )
    rules = load_rules(f)
    assert rules["category_mappings"]["sex"] == {"Male": "Male"}


def test_load_rules_still_rejects_chained_mapping_alongside_a_self_mapping(tmp_path):
    # Sanity check that excluding self-mappings from the intersection
    # doesn't accidentally blind the check to a genuine chain in a
    # different column of the same rules file.
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "category_mappings:\n"
        "  sex:\n"
        "    Male: Male\n"
        "  race:\n"
        "    M: Male\n"
        "    Male: Female\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_non_hashable_category_mapping_target(tmp_path):
    # A rules YAML with a list as a mapping target (e.g. category_mappings:
    # {age: {M: [a, b]}}) must not crash with an uncaught
    # `TypeError: unhashable type: 'list'` from set(mapping.values()) -- it
    # must be reported as a normal invalid-config error (RulesConfigError),
    # matching every other malformed-config case in this file.
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "category_mappings:\n"
        "  age:\n"
        "    M: [a, b]\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_non_hashable_missing_values_target(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  age:\n"
        "    \"99\": [a, b]\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_chained_missing_values(tmp_path):
    # {"99": "Refused", "Refused": "Unknown"} chains exactly like a chained
    # category_mappings entry: run 1 turns "99" -> "Refused"; run 2 on that
    # same output would turn "Refused" -> "Unknown". Must be rejected the
    # same way.
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  smoking_status:\n"
        "    \"99\": Refused\n"
        "    Refused: Unknown\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_empty_category_mappings_key(tmp_path):
    # "category_mappings:" present with nothing indented beneath it parses
    # to `None`, not `{}` -- setdefault does not replace an already-present
    # key, so this must be caught explicitly rather than crashing later
    # with an uncaught TypeError (e.g. `None.items()` / `set(None)`).
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns: {}\ncategory_mappings:\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_empty_missing_values_key(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns: {}\nmissing_values:\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_scalar_missing_values(tmp_path):
    # "missing_values: 5" -- a bare scalar instead of a mapping.
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns: {}\nmissing_values: 5\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_scalar_category_mappings(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns: {}\ncategory_mappings: 5\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_list_as_a_single_column_mapping(tmp_path):
    # "category_mappings:\n  sex: [M, F]" -- a list instead of a dict for
    # one column's mapping. The old code silently `continue`d past this
    # (leaving the list in place), which then crashed later in
    # apply_transformations with an uncaught TypeError (list indices must
    # be integers, not str) instead of failing cleanly here.
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "category_mappings:\n"
        "  sex: [M, F]\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_list_as_a_single_missing_values_mapping(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  status: [a, b]\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_empty_columns_key(tmp_path):
    # "columns:" present with nothing indented beneath it parses to `None`,
    # not `{}` -- and unlike missing_values/category_mappings, `columns` is
    # a REQUIRED key with no setdefault fallback, so this has the exact same
    # None-instead-of-dict failure mode and must be caught the same way,
    # rather than crashing later in validation.py's `columns_rules.items()`
    # with an uncaught AttributeError.
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns:\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_scalar_columns(tmp_path):
    # "columns: 5" -- a bare scalar instead of a mapping.
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns: 5\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_non_dict_single_column_ruleset(tmp_path):
    # "columns:\n  age: 5" -- a bare scalar instead of a column's rule-set
    # dict. Would otherwise crash later in validation.py's
    # `"minimum" in col_rules` with an uncaught TypeError.
    f = tmp_path / "rules.yaml"
    f.write_text("version: 1\nprimary_key: [id]\ncolumns:\n  age: 5\n")
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_non_numeric_minimum(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns:\n"
        "  age:\n"
        "    minimum: \"not-a-number\"\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_rejects_non_numeric_maximum(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns:\n"
        "  age:\n"
        "    maximum: \"not-a-number\"\n"
    )
    with pytest.raises(RulesConfigError):
        load_rules(f)


def test_load_rules_accepts_numeric_minimum_and_maximum(tmp_path):
    # Sanity check the new columns validation doesn't over-fire on an
    # ordinary, well-formed columns block (int and float bounds both
    # accepted).
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns:\n"
        "  age:\n"
        "    minimum: 0\n"
        "    maximum: 120.5\n"
    )
    rules = load_rules(f)
    assert rules["columns"]["age"]["minimum"] == 0
    assert rules["columns"]["age"]["maximum"] == 120.5


def test_load_rules_accepts_non_chained_missing_values(tmp_path):
    # Sanity check the missing_values chain check doesn't over-fire on an
    # ordinary sentinel mapping, matching schemas/cdc_wonder_rules.yaml's
    # real-world shape.
    f = tmp_path / "rules.yaml"
    f.write_text(
        "version: 1\n"
        "primary_key: [id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  deaths:\n"
        "    Suppressed: \"Suppressed (small-cell)\"\n"
        "    Not Applicable: Not Applicable\n"
    )
    rules = load_rules(f)
    assert rules["missing_values"]["deaths"]["Suppressed"] == "Suppressed (small-cell)"
