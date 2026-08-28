from dataforensics.investigate import (
    classify_column_types,
    detect_ambiguous_date_columns,
    detect_candidate_sentinels,
    detect_conflicting_id_records,
    detect_duplicate_entities,
    detect_duplicate_rows,
    detect_similar_categories,
    detect_survey_weight_columns,
    find_ambiguous_date_evidence,
    find_birth_date_after_other_date_evidence,
    find_category_value_evidence,
    find_fips_like_columns,
    find_implausible_value_evidence,
    find_invalid_fips_evidence,
    find_invalid_zip_evidence,
    find_sentinel_evidence,
    find_zip_like_columns,
    infer_semantic_role,
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


def test_detect_similar_categories_prefers_trimmed_value_as_canonical():
    # A plain alphabetical sort would pick "  Ada Lovelace" first (a
    # leading space sorts before a letter) -- backwards, since that
    # suggests merging the clean value INTO the messy one. The trimmed
    # value must be suggested instead.
    clusters = detect_similar_categories(["Ada Lovelace", "  Ada Lovelace"])
    assert len(clusters) == 1
    assert clusters[0]["suggested_canonical"] == "Ada Lovelace"


def test_detect_similar_categories_trailing_whitespace_canonical_unaffected():
    # Trailing whitespace already sorted correctly before this fix (a
    # shorter prefix sorts before the longer string it prefixes) --
    # confirms the fix didn't accidentally break that case.
    clusters = detect_similar_categories(["Bangalore", "Bangalore "])
    assert len(clusters) == 1
    assert clusters[0]["suggested_canonical"] == "Bangalore"


def test_detect_similar_categories_falls_back_to_alphabetical_when_no_member_trimmed():
    clusters = detect_similar_categories(["Refused", "Refuse", "Accepted"], threshold=80)
    assert len(clusters) == 1
    assert clusters[0]["suggested_canonical"] == "Refuse"


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


def test_infer_semantic_role_matches_date_of_birth_not_generic_date():
    assert infer_semantic_role("dob", {"category": "free_text"})["role"] == "DATE_OF_BIRTH"
    assert infer_semantic_role("date_of_birth", {"category": "free_text"})["role"] == "DATE_OF_BIRTH"
    assert infer_semantic_role("birth_date", {"category": "free_text"})["role"] == "DATE_OF_BIRTH"


def test_infer_semantic_role_matches_name_and_zip():
    assert infer_semantic_role("full_name", {"category": "free_text"})["role"] == "NAME"
    assert infer_semantic_role("zip_code", {"category": "categorical"})["role"] == "ZIP_OR_POSTAL"


def test_classify_column_types_identifier_from_dictionary_category():
    d = {"id": {"category": "id"}}
    rows = [{"id": "1"}, {"id": "2"}]
    assert classify_column_types(d, rows) == {"id": "identifier"}


def test_classify_column_types_date_when_all_values_date_shaped():
    d = {"visit_date": {"category": "free_text"}}
    rows = [{"visit_date": "2024-01-15"}, {"visit_date": "2024-02-20"}]
    assert classify_column_types(d, rows) == {"visit_date": "date"}


def test_classify_column_types_numeric_when_all_values_float_parseable():
    d = {"age": {"category": "free_text"}}
    rows = [{"age": "30"}, {"age": "45"}]
    assert classify_column_types(d, rows) == {"age": "numeric"}


def test_classify_column_types_categorical_when_not_date_or_numeric():
    d = {"sex": {"category": "categorical"}}
    rows = [{"sex": "M"}, {"sex": "F"}]
    assert classify_column_types(d, rows) == {"sex": "categorical"}


def test_classify_column_types_mixed_uncertain_for_free_text_and_empty():
    d = {"notes": {"category": "free_text"}, "empty": {"category": "free_text"}}
    rows = [{"notes": "some text here", "empty": ""}, {"notes": "other text", "empty": ""}]
    result = classify_column_types(d, rows)
    assert result["notes"] == "mixed_uncertain"
    assert result["empty"] == "mixed_uncertain"


def test_find_birth_date_after_other_date_evidence_flags_impossible_ordering():
    rows = [
        {"dob": "2020-01-01", "visit_date": "2024-01-01"},  # fine
        {"dob": "2024-06-01", "visit_date": "2024-01-01"},  # birth AFTER visit -- impossible
    ]
    evidence = find_birth_date_after_other_date_evidence(rows, "dob", "visit_date")
    assert evidence == [(1, "2024-06-01", "2024-01-01")]


def test_find_birth_date_after_other_date_evidence_skips_ambiguous_or_unparseable():
    rows = [
        {"dob": "13/01/2024", "visit_date": "2024-01-01"},  # dob not ISO -- skip
        {"dob": "2024-01-01", "visit_date": "not a date"},  # visit not ISO -- skip
    ]
    assert find_birth_date_after_other_date_evidence(rows, "dob", "visit_date") == []


def test_detect_duplicate_entities_finds_same_identity_different_ids():
    rows = [
        {"id": "1", "name": "Ada Lovelace", "dob": "1990-01-01", "zip": "94103"},
        {"id": "2", "name": "Ada Lovelace", "dob": "1990-01-01", "zip": "94103"},
        {"id": "3", "name": "Bob Smith", "dob": "1985-05-05", "zip": "10001"},
    ]
    dups = detect_duplicate_entities(rows, ["name", "dob", "zip"], "id")
    assert len(dups) == 1
    assert dups[0]["row_indices"] == [0, 1]
    assert dups[0]["id_values"] == ["1", "2"]


def test_detect_duplicate_entities_normalizes_case_and_whitespace():
    rows = [
        {"id": "1", "name": "Ada Lovelace"},
        {"id": "2", "name": "  ada lovelace  "},
    ]
    dups = detect_duplicate_entities(rows, ["name"], "id")
    assert len(dups) == 1


def test_detect_duplicate_entities_no_flag_when_same_id():
    rows = [
        {"id": "1", "name": "Ada Lovelace"},
        {"id": "1", "name": "Ada Lovelace"},
    ]
    assert detect_duplicate_entities(rows, ["name"], "id") == []


def test_detect_duplicate_entities_skips_rows_with_incomplete_quasi_identifiers():
    rows = [
        {"id": "1", "name": "Ada Lovelace", "zip": ""},
        {"id": "2", "name": "Ada Lovelace", "zip": ""},
    ]
    assert detect_duplicate_entities(rows, ["name", "zip"], "id") == []
