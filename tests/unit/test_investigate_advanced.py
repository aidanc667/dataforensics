from dataforensics.investigate import (
    check_referential_integrity,
    compare_fingerprints,
    compute_dataset_fingerprint,
    discover_shared_key_columns,
    infer_semantic_role,
)


# --------------------------------------------------------------------- #
# infer_semantic_role
# --------------------------------------------------------------------- #

def test_infer_semantic_role_matches_age():
    result = infer_semantic_role("age", {"category": "free_text"})
    assert result["role"] == "AGE"


def test_infer_semantic_role_matches_age_with_suffix():
    result = infer_semantic_role("age_at_diagnosis", {"category": "free_text"})
    assert result["role"] == "AGE"


def test_infer_semantic_role_no_false_positive_on_substring():
    # "wage", "average", "usage" all contain "age" as a bare substring but
    # not as a boundary-delimited token -- must not fire.
    for name in ("wage", "average_score", "usage_count"):
        assert infer_semantic_role(name, {"category": "free_text"}) is None


def test_infer_semantic_role_matches_sex_and_date():
    assert infer_semantic_role("sex", {"category": "categorical"})["role"] == "SEX_OR_GENDER"
    assert infer_semantic_role("visit_date", {"category": "free_text"})["role"] == "DATE"


def test_infer_semantic_role_skips_id_columns():
    assert infer_semantic_role("participant_id", {"category": "id"}) is None


def test_infer_semantic_role_none_for_unmatched_column():
    assert infer_semantic_role("smoking_status", {"category": "free_text"}) is None


def test_infer_semantic_role_confidence_is_qualitative_never_numeric():
    result = infer_semantic_role("age", {"category": "free_text"})
    assert result["confidence"] in ("high", "medium")
    assert not isinstance(result["confidence"], (int, float))


# --------------------------------------------------------------------- #
# fingerprinting
# --------------------------------------------------------------------- #

def test_compute_dataset_fingerprint_deterministic():
    d = {"age": {"dtype": "Utf8", "category": "free_text", "non_null_pct": 100.0, "unique_count": 5, "is_zero_variance": False}}
    fp1 = compute_dataset_fingerprint(d, row_count=10)
    fp2 = compute_dataset_fingerprint(d, row_count=10)
    assert fp1 == fp2


def test_compute_dataset_fingerprint_changes_with_schema():
    d1 = {"age": {"dtype": "Utf8", "category": "free_text", "non_null_pct": 100.0, "unique_count": 5, "is_zero_variance": False}}
    d2 = {"age": {"dtype": "Utf8", "category": "free_text", "non_null_pct": 100.0, "unique_count": 5, "is_zero_variance": False},
          "sex": {"dtype": "Utf8", "category": "categorical", "non_null_pct": 100.0, "unique_count": 2, "is_zero_variance": False}}
    fp1 = compute_dataset_fingerprint(d1, row_count=10)
    fp2 = compute_dataset_fingerprint(d2, row_count=10)
    assert fp1["schema_fingerprint"] != fp2["schema_fingerprint"]


def test_compare_fingerprints_detects_added_column_and_missingness_change():
    prev_dict = {"age": {"dtype": "Utf8", "category": "free_text", "non_null_pct": 100.0, "unique_count": 5, "is_zero_variance": False}}
    curr_dict = {
        "age": {"dtype": "Utf8", "category": "free_text", "non_null_pct": 90.0, "unique_count": 5, "is_zero_variance": False},
        "sex": {"dtype": "Utf8", "category": "categorical", "non_null_pct": 100.0, "unique_count": 2, "is_zero_variance": False},
    }
    prev_fp = compute_dataset_fingerprint(prev_dict, row_count=100)
    curr_fp = compute_dataset_fingerprint(curr_dict, row_count=120)

    diff = compare_fingerprints(prev_fp, curr_fp, prev_dict, curr_dict)
    assert diff["columns_added"] == ["sex"]
    assert diff["columns_removed"] == []
    assert diff["row_count_delta"] == 20
    assert diff["schema_changed"] is True
    assert any(c["column"] == "age" for c in diff["changed_columns"])


def test_compare_fingerprints_no_diff_when_identical():
    d = {"age": {"dtype": "Utf8", "category": "free_text", "non_null_pct": 100.0, "unique_count": 5, "is_zero_variance": False}}
    fp = compute_dataset_fingerprint(d, row_count=10)
    diff = compare_fingerprints(fp, fp, d, d)
    assert diff["schema_changed"] is False
    assert diff["columns_added"] == []
    assert diff["columns_removed"] == []
    assert diff["row_count_delta"] == 0
    assert diff["changed_columns"] == []


# --------------------------------------------------------------------- #
# cross-file relationship discovery
# --------------------------------------------------------------------- #

def test_discover_shared_key_columns_finds_high_overlap_match():
    file_rows = {
        "participants.csv": [{"participant_id": "1", "age": "30"}, {"participant_id": "2", "age": "40"}],
        "labs.csv": [{"ParticipantID": "1", "value": "5.0"}, {"ParticipantID": "2", "value": "6.0"}],
    }
    candidates = discover_shared_key_columns(file_rows)
    assert len(candidates) == 1
    assert candidates[0]["column_a"] == "participant_id"
    assert candidates[0]["column_b"] == "ParticipantID"
    assert candidates[0]["overlap_fraction"] == 1.0


def test_discover_shared_key_columns_ignores_low_overlap():
    file_rows = {
        "a.csv": [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}],
        "b.csv": [{"id": "999"}, {"id": "1"}],
    }
    candidates = discover_shared_key_columns(file_rows, min_overlap=0.9)
    assert candidates == []


def test_check_referential_integrity_finds_orphans():
    parent = {"1", "2", "3"}
    child = {"1", "2", "99"}
    result = check_referential_integrity(parent, child)
    assert result["orphan_count"] == 1
    assert result["orphan_examples"] == ["99"]


def test_check_referential_integrity_no_orphans():
    parent = {"1", "2", "3"}
    child = {"1", "2"}
    result = check_referential_integrity(parent, child)
    assert result["orphan_count"] == 0
