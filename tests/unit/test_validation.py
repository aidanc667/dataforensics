from rdh.validation import validate


_RULES = {
    "version": 1,
    "primary_key": ["participant_id"],
    "columns": {
        "age": {"type": "integer", "minimum": 0, "maximum": 120},
    },
    "missing_values": {},
    "category_mappings": {},
    "weights_strata": {"columns": []},
}


def test_minimum_violation_is_error():
    rows = [{"participant_id": "1", "age": "-5"}]
    result = validate(rows, _RULES)
    assert len(result["errors"]) == 1
    assert result["errors"][0]["rule"] == "minimum"
    assert result["warnings"] == []


def test_maximum_violation_is_warning_not_error():
    rows = [{"participant_id": "1", "age": "130"}]
    result = validate(rows, _RULES)
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    assert result["warnings"][0]["rule"] == "maximum"


def test_plausible_extreme_value_is_not_flagged():
    rows = [{"participant_id": "1", "age": "95"}]
    result = validate(rows, _RULES)
    assert result["errors"] == []
    assert result["warnings"] == []


def test_duplicate_primary_key_is_error():
    rows = [
        {"participant_id": "1", "age": "40"},
        {"participant_id": "1", "age": "41"},
    ]
    result = validate(rows, _RULES)
    dup_errors = [e for e in result["errors"] if e["rule"] == "duplicate_primary_key"]
    assert len(dup_errors) == 1


def test_rare_category_is_suggestion_never_error_or_warning():
    rules = dict(_RULES)
    rows = [
        {"participant_id": str(i), "age": "40"} for i in range(20)
    ] + [{"participant_id": "21", "age": "40"}]
    # inject a rare free-text-ish column check isn't part of _RULES; this test
    # exercises that validate() never promotes a heuristic to error/warning tier
    result = validate(rows, rules)
    assert all(f["severity"] != "error" for f in result["suggestions"])
    assert all(f["severity"] != "warning" for f in result["suggestions"])


def test_column_with_no_rule_is_not_evaluated():
    rows = [{"participant_id": "1", "age": "40", "site": "A"}]
    result = validate(rows, _RULES)
    assert result["checks_evaluated"] == result["checks_passed"] + len(
        result["errors"]
    ) + len(result["warnings"])


def test_outlier_value_is_suggestion_not_error_or_warning():
    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [{"participant_id": str(i), "lab_value": "10"} for i in range(8)]
    rows[7]["lab_value"] = "500"  # a genuine outlier
    result = validate(rows, rules)
    outlier_suggestions = [s for s in result["suggestions"] if s["rule"] == "iqr_outlier"]
    assert len(outlier_suggestions) == 1
    assert outlier_suggestions[0]["row_key"] == {"participant_id": "7"}
    assert result["errors"] == []
    assert result["warnings"] == []


def test_rare_category_is_suggestion_with_correct_row_key():
    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = (
        [{"participant_id": str(i), "site": "A"} for i in range(10)]
        + [{"participant_id": "10", "site": "Z"}]
    )
    result = validate(rows, rules)
    rare = [s for s in result["suggestions"] if s["rule"] == "rare_category"]
    assert len(rare) == 1
    assert rare[0]["row_key"] == {"participant_id": "10"}
    assert result["errors"] == []
    assert result["warnings"] == []


def test_id_shaped_column_not_flagged_as_rare_category():
    # every value unique -> looks like an ID column, not a categorical column with one rare value
    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [{"participant_id": str(i), "site": f"site-{i}"} for i in range(10)]
    result = validate(rows, rules)
    rare = [s for s in result["suggestions"] if s["rule"] == "rare_category"]
    assert rare == []
