from dataforensics.validation import validate
from dataforensics.typing_guards import is_pii_like_column


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


def test_high_cardinality_column_not_flagged_as_rare_category():
    # 100 rows, every "site" value unique -> unique_count (100) exceeds
    # dictionary.cardinality_cap(100) (10, the small-N floor), so
    # dictionary.py itself would classify this column "free_text", not
    # "categorical" -- validation.py's rare-category heuristic must reach
    # the same conclusion using the same cap, not an independent threshold
    # (a real, confirmed divergence: see cardinality_cap's own docstring
    # for the ACS PUMS SERIALNO case this guards against). At only 10 rows
    # the floor would make even a fully-unique column count as
    # "categorical" in both modules -- this test uses enough rows that the
    # ratio genuinely dominates the floor, so the distinction is
    # unambiguous.
    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [{"participant_id": str(i), "site": f"site-{i}"} for i in range(100)]
    result = validate(rows, rules)
    rare = [s for s in result["suggestions"] if s["rule"] == "rare_category"]
    assert rare == []


def test_rare_category_cardinality_threshold_matches_dictionary_classification():
    # Regression test for a real bug found running this tool against a real
    # ACS PUMS extract: a household-identifier column (SERIALNO) has high
    # but not maximal cardinality (multiple people share one household's
    # serial number), so dictionary.py's cardinality_cap-based
    # classification correctly calls it "free_text" -- but validation.py's
    # OLD independent "unique_count < half the rows" threshold was far more
    # permissive and still called it categorical, firing a misleading
    # "rare category" suggestion on every single-person household. This
    # test reproduces that exact shape (high, non-maximal cardinality) at
    # a scale where the two thresholds genuinely disagreed, and pins down
    # that they must not anymore.
    from dataforensics.dictionary import build_data_dictionary, cardinality_cap

    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    # 200 rows, ~90 distinct household ids (each shared by ~2 people) --
    # under half of 200 (the old threshold's cutoff) but well above
    # cardinality_cap(200).
    household_ids = [f"H{i:04d}" for i in range(90)]
    rows = [
        {"participant_id": str(i), "household_ref": household_ids[i % len(household_ids)]}
        for i in range(200)
    ]
    assert cardinality_cap(200) < 90  # sanity: this shape genuinely exercises the divergence

    result = validate(rows, rules)
    rare = [s for s in result["suggestions"] if s["rule"] == "rare_category" and s["column"] == "household_ref"]
    assert rare == []


def test_rare_category_never_fires_on_pii_like_column():
    # patient_name is a PII-like column name (is_pii_like_column). Earlier
    # rounds masked a rare value's raw text in the finding message via
    # _display_value, but still let the suggestion fire in the first place
    # -- which contradicted dictionary.py's treatment of the same column
    # (dictionary.py never numerically/statistically profiles a masked
    # PII-like column at all). The guard now excludes PII-like columns from
    # rare_category candidacy entirely, the same way it already excluded
    # ID-like columns, so no finding -- masked or not -- is produced for
    # this column.
    assert is_pii_like_column("patient_name") is True
    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = (
        [{"participant_id": str(i), "patient_name": "Jane Doe"} for i in range(10)]
        + [{"participant_id": "10", "patient_name": "Zelda Uniquename"}]
    )
    result = validate(rows, rules)
    rare = [s for s in result["suggestions"] if s["rule"] == "rare_category"]
    assert rare == []


def test_duplicate_primary_key_message_masks_pii_like_primary_key_value():
    # If the primary key itself is a PII-like column (e.g. ssn used as the
    # dataset's unique identifier), the duplicated key value must not leak
    # into the finding message either.
    assert is_pii_like_column("ssn") is True
    rules = {
        "version": 1,
        "primary_key": ["ssn"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [
        {"ssn": "123-45-6789", "age": "40"},
        {"ssn": "123-45-6789", "age": "41"},
    ]
    result = validate(rows, rules)
    dup_errors = [e for e in result["errors"] if e["rule"] == "duplicate_primary_key"]
    assert len(dup_errors) == 1
    assert "123-45-6789" not in dup_errors[0]["message"]
    assert "[masked" in dup_errors[0]["message"]


def test_id_like_numeric_column_never_flagged_as_outlier():
    # county_fips is id-like (matches typing_guards.is_id_like_column) and
    # its values happen to parse as floats ("06081" -> 6081.0), but it must
    # never enter the IQR-outlier path -- dictionary.py already classifies
    # such columns as category "id" and never numerically casts or
    # outlier-tests them; validation.py's suggestion-tier check must match.
    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [
        {"participant_id": "1", "county_fips": "06081"},
        {"participant_id": "2", "county_fips": "06001"},
        {"participant_id": "3", "county_fips": "02138"},
        {"participant_id": "4", "county_fips": "48201"},  # would be a numeric outlier if cast
        {"participant_id": "5", "county_fips": "06081"},
        {"participant_id": "6", "county_fips": "02138"},
        {"participant_id": "7", "county_fips": "06001"},
        {"participant_id": "8", "county_fips": "48201"},
    ]
    result = validate(rows, rules)
    outlier_suggestions = [s for s in result["suggestions"] if s["rule"] == "iqr_outlier" and s["column"] == "county_fips"]
    assert outlier_suggestions == []


def test_leading_zero_id_like_column_never_flagged_as_outlier_even_without_id_like_name():
    # "county_code" does NOT match is_id_like_column's naming pattern, but
    # its values ("06081", "02138", "48201", ...) preserve leading zeros --
    # dictionary.py classifies such a column as category "id" via
    # `is_id_like_column(name) OR preserves_leading_zero(values)`, not via
    # the name pattern alone. Before the fix, validation.py's guard only
    # checked is_id_like_column(column), so this column would still be
    # float()-cast (destroying the leading zero) and IQR-tested here,
    # contradicting dictionary.py's own classification of the same column.
    from dataforensics.typing_guards import is_id_like_column, preserves_leading_zero

    assert is_id_like_column("county_code") is False
    assert preserves_leading_zero(["06081", "02138", "48201"]) is True

    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [
        {"participant_id": "1", "county_code": "06081"},
        {"participant_id": "2", "county_code": "06001"},
        {"participant_id": "3", "county_code": "02138"},
        {"participant_id": "4", "county_code": "48201"},  # would be a numeric outlier if cast
        {"participant_id": "5", "county_code": "06081"},
        {"participant_id": "6", "county_code": "02138"},
        {"participant_id": "7", "county_code": "06001"},
        {"participant_id": "8", "county_code": "48201"},
    ]
    result = validate(rows, rules)
    outlier_suggestions = [
        s for s in result["suggestions"] if s["rule"] == "iqr_outlier" and s["column"] == "county_code"
    ]
    assert outlier_suggestions == []


def test_leading_zero_low_cardinality_column_not_flagged_as_rare_category():
    # Same contradiction as the outlier case above, but for rare_category:
    # a low-cardinality leading-zero-preserving column (only 2 distinct
    # values across many rows) satisfies the "looks categorical" cardinality
    # heuristic and, before the fix, would get its singleton value flagged
    # as rare_category -- even though dictionary.py classifies this column
    # as category "id" via preserves_leading_zero, not "categorical".
    from dataforensics.typing_guards import is_id_like_column, preserves_leading_zero

    assert is_id_like_column("county_code") is False
    assert preserves_leading_zero(["06081", "02138"]) is True

    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [{"participant_id": str(i), "county_code": "06081"} for i in range(8)] + [
        {"participant_id": "8", "county_code": "02138"}  # would look "rare" if treated as categorical
    ]
    result = validate(rows, rules)
    rare = [s for s in result["suggestions"] if s["rule"] == "rare_category" and s["column"] == "county_code"]
    assert rare == []


def test_pii_like_column_never_flagged_as_outlier():
    # "phone" is PII-like (is_pii_like_column) and its values happen to
    # parse as floats, but dictionary.py never numerically casts/
    # outlier-tests a column when `category != "id" and not mask_pii` is
    # false -- i.e. it also excludes PII-like columns, not just ID-like
    # ones. validation.py's suggestion-tier guard must match that full
    # condition, not just the ID half of it.
    from dataforensics.typing_guards import is_id_like_column, is_pii_like_column, preserves_leading_zero

    assert is_pii_like_column("phone") is True
    assert is_id_like_column("phone") is False
    assert preserves_leading_zero(["5551234", "9999999"]) is False

    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [
        {"participant_id": "1", "phone": "5551234"},
        {"participant_id": "2", "phone": "5551235"},
        {"participant_id": "3", "phone": "5551236"},
        {"participant_id": "4", "phone": "9999999"},  # would be a numeric outlier if cast
        {"participant_id": "5", "phone": "5551234"},
        {"participant_id": "6", "phone": "5551236"},
        {"participant_id": "7", "phone": "5551235"},
        {"participant_id": "8", "phone": "9999999"},
    ]
    result = validate(rows, rules)
    outlier_suggestions = [
        s for s in result["suggestions"] if s["rule"] == "iqr_outlier" and s["column"] == "phone"
    ]
    assert outlier_suggestions == []


def test_pii_like_low_cardinality_column_not_flagged_as_rare_category():
    # Same contradiction as the outlier case above, but for rare_category:
    # a low-cardinality PII-like column satisfies the "looks categorical"
    # cardinality heuristic and, before the fix, would get its singleton
    # value flagged as rare_category -- even though the message is
    # separately masked, the suggestion firing at all on a PII column
    # contradicts dictionary.py's treatment of the same column.
    from dataforensics.typing_guards import is_pii_like_column

    assert is_pii_like_column("phone") is True

    rules = {
        "version": 1,
        "primary_key": ["participant_id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [{"participant_id": str(i), "phone": "5551234"} for i in range(8)] + [
        {"participant_id": "8", "phone": "5559999"}  # would look "rare" if treated as categorical
    ]
    result = validate(rows, rules)
    rare = [s for s in result["suggestions"] if s["rule"] == "rare_category" and s["column"] == "phone"]
    assert rare == []


def test_composite_primary_key_duplicate_detection():
    rules = {
        "version": 1,
        "primary_key": ["participant_id", "visit_date"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        "weights_strata": {"columns": []},
    }
    rows = [
        {"participant_id": "1", "visit_date": "2024-01-01", "age": "40"},
        {"participant_id": "1", "visit_date": "2024-06-01", "age": "41"},  # same participant, different visit -> NOT a duplicate
        {"participant_id": "1", "visit_date": "2024-01-01", "age": "99"},  # exact same (participant_id, visit_date) -> IS a duplicate
    ]
    result = validate(rows, rules)
    dup_errors = [e for e in result["errors"] if e["rule"] == "duplicate_primary_key"]
    assert len(dup_errors) == 1
    assert dup_errors[0]["row_key"] == {"participant_id": "1", "visit_date": "2024-01-01"}
