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
