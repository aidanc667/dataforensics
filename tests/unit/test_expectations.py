import pytest

from dataforensics.expectations import (
    describe,
    evaluate_expectation,
    evaluate_expectations,
    validate_expectation,
)

COLS = ["id", "age", "sex", "pregnant", "email"]
ROWS = [
    {"id": "1", "age": "34", "sex": "F", "pregnant": "Yes", "email": "a@x.com"},
    {"id": "2", "age": "150", "sex": "M", "pregnant": "Yes", "email": "b@x.com"},
    {"id": "2", "age": "", "sex": "X", "pregnant": "", "email": ""},
    {"id": "4", "age": "unknown", "sex": "M", "pregnant": "", "email": "a@x.com"},
]


def test_range_counts_violations_and_unevaluable_separately():
    r = evaluate_expectation(ROWS, {"type": "range", "column": "age", "min": 0, "max": 120})
    assert r["violations"] == 1
    assert r["evidence"] == [(1, "age = 150")]
    assert r["not_evaluable"] == 1  # "unknown" is neither a pass nor a fail
    assert r["evaluated"] == 2  # blank skipped


def test_required_unique_allowed():
    assert evaluate_expectation(ROWS, {"type": "required", "column": "age"})["violations"] == 1
    unique = evaluate_expectation(ROWS, {"type": "unique", "column": "id"})
    assert unique["violations"] == 2
    assert unique["evidence"][0] == (1, "id = 2 appears 2 times")
    allowed = evaluate_expectation(ROWS, {"type": "allowed_values", "column": "sex", "values": ["F", "M"]})
    assert allowed["evidence"] == [(2, "sex = X")]


def test_conditional_skip_logic():
    spec = {"type": "conditional", "if_column": "sex", "if_op": "=", "if_value": "M", "then_column": "pregnant", "then_op": "is blank"}
    r = evaluate_expectation(ROWS, spec)
    assert r["violations"] == 1
    assert r["evidence"] == [(1, "sex = M, pregnant = Yes")]
    assert describe(spec) == "if sex = M then pregnant is blank"


def test_conditional_numeric_operator_with_non_number_is_not_evaluable():
    spec = {"type": "conditional", "if_column": "age", "if_op": "<", "if_value": "15", "then_column": "pregnant", "then_op": "is blank"}
    r = evaluate_expectation(ROWS, spec)
    assert r["violations"] == 0
    assert r["not_evaluable"] == 2  # blank age and "unknown"


def test_pii_columns_are_masked_in_evidence():
    r = evaluate_expectation(ROWS, {"type": "unique", "column": "email"})
    assert all("a@x.com" not in e for _, e in r["evidence"])


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "nope", "column": "age"},
        {"type": "range", "column": "missing_col", "min": 0},
        {"type": "range", "column": "age"},
        {"type": "range", "column": "age", "min": 10, "max": 5},
        {"type": "range", "column": "age", "min": "zero"},
        {"type": "allowed_values", "column": "sex", "values": []},
        {"type": "conditional", "if_column": "sex", "if_op": "~", "if_value": "M", "then_column": "pregnant", "then_op": "is blank"},
        {"type": "conditional", "if_column": "sex", "if_op": "=", "then_column": "pregnant", "then_op": "is blank"},
        {"type": "conditional", "if_column": "age", "if_op": "<", "if_value": "abc", "then_column": "pregnant", "then_op": "is blank"},
    ],
)
def test_malformed_specs_raise(spec):
    with pytest.raises(ValueError):
        validate_expectation(spec, COLS)


def test_evaluate_expectations_names_the_bad_rule():
    with pytest.raises(ValueError, match="expectation #2"):
        evaluate_expectations(ROWS, [{"type": "required", "column": "id"}, {"type": "unique", "column": "zzz"}], COLS)
