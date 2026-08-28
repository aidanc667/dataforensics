from dataforensics.investigate import (
    detect_ambiguous_date_columns,
    detect_candidate_sentinels,
    detect_conflicting_id_records,
    detect_duplicate_rows,
    detect_similar_categories,
    detect_survey_weight_columns,
    find_ambiguous_date_evidence,
    find_category_value_evidence,
    find_fips_like_columns,
    find_implausible_value_evidence,
    find_invalid_fips_evidence,
    find_invalid_zip_evidence,
    find_sentinel_evidence,
    find_zip_like_columns,
    match_clinical_range_rule,
)


def test_detect_duplicate_rows_finds_exact_repeats():
    rows = [
        {"id": "1", "age": "30"},
        {"id": "2", "age": "40"},
        {"id": "1", "age": "30"},  # exact duplicate of row 0
    ]
    dups = detect_duplicate_rows(rows)
    assert len(dups) == 1
    assert dups[0]["row_index"] == 2
    assert dups[0]["duplicate_of_row_index"] == 0


def test_detect_duplicate_rows_no_false_positive_on_distinct_rows():
    rows = [{"id": "1", "age": "30"}, {"id": "2", "age": "30"}]
    assert detect_duplicate_rows(rows) == []


def test_detect_candidate_sentinels_finds_common_codes():
    rows = [{"smoking": "99"}, {"smoking": "5"}, {"income": "N/A"}]
    found = detect_candidate_sentinels(rows, ["smoking", "income"])
    assert found["smoking"] == ["99"]
    assert found["income"] == ["N/A"]


def test_detect_candidate_sentinels_no_false_positive_on_ordinary_values():
    rows = [{"age": "34"}, {"age": "29"}]
    assert detect_candidate_sentinels(rows, ["age"]) == {}


def test_detect_ambiguous_date_columns_flags_slash_dates():
    rows = [{"visit": "03/04/2024"}, {"visit": "2024-01-01"}]
    found = detect_ambiguous_date_columns(rows, ["visit"])
    assert found == {"visit": 1}


def test_detect_ambiguous_date_columns_no_flag_when_all_iso():
    rows = [{"visit": "2024-01-01"}, {"visit": "2024-02-02"}]
    assert detect_ambiguous_date_columns(rows, ["visit"]) == {}


def test_detect_ambiguous_date_columns_flags_dash_dates():
    rows = [{"visit": "03-04-2024"}, {"visit": "2024-01-01"}]
    found = detect_ambiguous_date_columns(rows, ["visit"])
    assert found == {"visit": 1}


def test_find_sentinel_evidence_returns_real_row_indices():
    rows = [{"status": "10"}, {"status": "99"}, {"status": "5"}, {"status": "99"}]
    assert find_sentinel_evidence(rows, "status", "99") == [1, 3]


def test_find_sentinel_evidence_matches_after_stripping_whitespace():
    rows = [{"status": " 99 "}, {"status": "10"}]
    assert find_sentinel_evidence(rows, "status", "99") == [0]


def test_find_ambiguous_date_evidence_returns_real_rows_and_values():
    rows = [{"visit": "03/04/2024"}, {"visit": "2024-01-01"}, {"visit": "01/02/2024"}]
    assert find_ambiguous_date_evidence(rows, "visit") == [(0, "03/04/2024"), (2, "01/02/2024")]


def test_find_category_value_evidence_returns_real_row_indices():
    rows = [{"sex": "male"}, {"sex": "Male"}, {"sex": "female"}, {"sex": "male"}]
    assert find_category_value_evidence(rows, "sex", "male") == [0, 3]


def test_detect_similar_categories_high_confidence_on_case_variants():
    clusters = detect_similar_categories(["Male", "male", "MALE", "Female"])
    assert len(clusters) == 1
    assert set(clusters[0]["values"]) == {"MALE", "Male", "male"}
    assert clusters[0]["confidence"] == "high"


def test_detect_similar_categories_medium_confidence_on_fuzzy_not_exact():
    clusters = detect_similar_categories(["Refused", "Refuse", "Accepted"], threshold=80)
    assert len(clusters) == 1
    assert clusters[0]["confidence"] == "medium"


def test_detect_similar_categories_no_cluster_for_dissimilar_values():
    clusters = detect_similar_categories(["Male", "Female", "Unknown"])
    assert clusters == []


def test_detect_similar_categories_skips_high_cardinality_columns():
    values = [f"note-{i}" for i in range(60)]
    assert detect_similar_categories(values) == []


def test_match_clinical_range_rule_matches_age_column():
    rule = match_clinical_range_rule("age")
    assert rule["min"] == 0
    assert rule["max"] == 120


def test_match_clinical_range_rule_matches_age_with_prefix_suffix():
    assert match_clinical_range_rule("participant_age_years") is not None


def test_match_clinical_range_rule_no_match_for_unrelated_column():
    assert match_clinical_range_rule("participant_id") is None


def test_find_implausible_value_evidence_flags_out_of_range_values():
    rows = [{"age": "30"}, {"age": "300"}, {"age": "-4"}, {"age": "45"}]
    evidence = find_implausible_value_evidence(rows, "age", min_value=0, max_value=120)
    assert evidence == [(1, "300"), (2, "-4")]


def test_find_implausible_value_evidence_skips_non_numeric_values():
    rows = [{"age": "30"}, {"age": "unknown"}]
    assert find_implausible_value_evidence(rows, "age", min_value=0, max_value=120) == []


def test_detect_conflicting_id_records_finds_same_id_different_fields():
    rows = [
        {"participant_id": "1", "age": "30"},
        {"participant_id": "1", "age": "31"},  # same id, conflicting age
        {"participant_id": "2", "age": "40"},
    ]
    conflicts = detect_conflicting_id_records(rows, "participant_id")
    assert len(conflicts) == 1
    assert conflicts[0]["id_value"] == "1"
    assert conflicts[0]["row_indices"] == [0, 1]


def test_detect_conflicting_id_records_no_conflict_for_exact_repeats():
    rows = [{"participant_id": "1", "age": "30"}, {"participant_id": "1", "age": "30"}]
    assert detect_conflicting_id_records(rows, "participant_id") == []


def test_find_invalid_fips_evidence_flags_wrong_length_codes():
    rows = [{"fips": "06"}, {"fips": "06037"}, {"fips": "123"}, {"fips": "abcde"}]
    evidence = find_invalid_fips_evidence(rows, "fips")
    assert evidence == [(2, "123"), (3, "abcde")]


def test_find_invalid_zip_evidence_flags_wrong_shape_codes():
    rows = [{"zip": "94103"}, {"zip": "94103-1234"}, {"zip": "941"}, {"zip": "abcde"}]
    evidence = find_invalid_zip_evidence(rows, "zip")
    assert evidence == [(2, "941"), (3, "abcde")]


def test_detect_survey_weight_columns_matches_common_patterns():
    columns = ["participant_id", "wt_final", "age", "wgt2011", "final_weight"]
    assert detect_survey_weight_columns(columns) == ["wt_final", "wgt2011", "final_weight"]


def test_detect_survey_weight_columns_no_false_positive():
    columns = ["participant_id", "age", "weightless_flag_unrelated"]
    # "weightless_flag_unrelated" doesn't match the (^|_)weight(_|$) boundary
    assert detect_survey_weight_columns(columns) == []


def test_find_fips_like_columns_matches_case_insensitively():
    columns = ["participant_id", "county_fips", "FIPS_code", "age"]
    assert find_fips_like_columns(columns) == ["county_fips", "FIPS_code"]


def test_find_zip_like_columns_matches_case_insensitively():
    columns = ["participant_id", "zip_code", "ZIPCODE", "age"]
    assert find_zip_like_columns(columns) == ["zip_code", "ZIPCODE"]
