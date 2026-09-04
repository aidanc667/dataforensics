from dataforensics.investigate import (
    analyze_key_cardinality,
    build_missingness_overview,
    classify_column_types,
    compute_key_coverage,
    detect_age_columns_that_look_like_years,
    detect_ambiguous_date_columns,
    detect_candidate_sentinels,
    detect_column_order_violations,
    detect_conflicting_id_records,
    detect_duplicate_entities,
    detect_duplicate_rows,
    detect_encoding_corruption,
    detect_invisible_characters,
    detect_missingness_co_occurrence,
    detect_missingness_concentration,
    detect_numeric_representation_inconsistency,
    detect_similar_categories,
    detect_survey_weight_columns,
    detect_unit_inconsistency,
    detect_value_shape_outliers,
    detect_whitespace_anomalies,
    find_ambiguous_date_evidence,
    find_birth_date_after_other_date_evidence,
    find_category_value_evidence,
    find_column_order_violation_evidence,
    find_encoding_corruption_evidence,
    find_fips_like_columns,
    find_implausible_value_evidence,
    find_invalid_fips_evidence,
    find_invalid_zip_evidence,
    find_invisible_character_evidence,
    find_numeric_representation_evidence,
    find_ordered_column_pairs,
    find_sentinel_evidence,
    find_unit_inconsistency_evidence,
    find_value_shape_outlier_evidence,
    find_whitespace_anomaly_evidence,
    find_year_like_value_evidence,
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
    # "-99" appears in only 1 of 10 rows (10%, well under the dominance
    # threshold) -- a plausible rare missing-value code, not a common
    # legitimate value.
    rows = [{"smoking": "-99"}] + [{"smoking": str(i)} for i in range(9)]
    rows += [{"income": "N/A"}] + [{"income": str(i)} for i in range(9)]
    found = detect_candidate_sentinels(rows, ["smoking", "income"])
    assert found["smoking"] == ["-99"]
    assert found["income"] == ["N/A"]


def test_detect_candidate_sentinels_no_false_positive_on_ordinary_values():
    rows = [{"age": "34"}, {"age": "29"}]
    assert detect_candidate_sentinels(rows, ["age"]) == {}


def test_detect_candidate_sentinels_never_flags_bare_99():
    # Bare "99" is excluded from COMMON_SENTINEL_STRINGS entirely -- it's
    # an extremely common legitimate value in its own right (a
    # neighborhood/district code, a percentile, a real category id) far
    # more often than it's actually a missing-value marker. Must not be
    # flagged even where it's genuinely rare, unlike "-99"/"999"/"9999".
    rows = [{"neighborhood": "99"}] + [{"neighborhood": str(i)} for i in range(99)]
    assert detect_candidate_sentinels(rows, ["neighborhood"]) == {}


def test_detect_candidate_sentinels_does_not_flag_a_dominant_value():
    # "-99" is genuinely common here (6 of 10 rows, 60%) -- unusual for
    # an actual missing-value convention. A real missing-value convention
    # essentially never dominates a column like this, so it must not be
    # flagged even though "-99" itself is still a recognized pattern.
    rows = [{"code": "-99"}] * 6 + [{"code": str(i)} for i in range(4)]
    assert detect_candidate_sentinels(rows, ["code"]) == {}


def test_detect_candidate_sentinels_flags_right_at_the_threshold_boundary():
    # Exactly 25% (2 of 8) is still flagged (<=, not <) -- one below that
    # share should not be.
    rows = [{"code": "-99"}] * 2 + [{"code": str(i)} for i in range(6)]
    assert detect_candidate_sentinels(rows, ["code"]) == {"code": ["-99"]}

    rows_just_over = [{"code": "-99"}] * 3 + [{"code": str(i)} for i in range(6)]
    assert detect_candidate_sentinels(rows_just_over, ["code"]) == {}


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


def test_detect_similar_categories_does_not_cluster_negation_pairs():
    # Regression: rapidfuzz scores "employed"/"unemployed" at 88.9%,
    # above the default 85% threshold, but they're opposite categories --
    # merging them would silently corrupt the data. Same for other
    # standard negation prefixes a researcher's survey data could contain.
    assert detect_similar_categories(["employed", "unemployed"]) == []
    assert detect_similar_categories(["consistent", "inconsistent"]) == []
    assert detect_similar_categories(["legal", "illegal"]) == []
    assert detect_similar_categories(["satisfied", "dissatisfied"]) == []


def test_detect_similar_categories_negation_check_does_not_break_real_typo_clusters():
    # Confirms the negation guard is narrowly scoped -- genuine typo
    # clusters (no negation prefix relationship) must still work.
    clusters = detect_similar_categories(["Male", "male", "MALE"])
    assert len(clusters) == 1
    assert set(clusters[0]["values"]) == {"Male", "male", "MALE"}


def test_detect_similar_categories_falls_back_to_alphabetical_when_no_member_trimmed():
    clusters = detect_similar_categories(["Refused", "Refuse", "Accepted"], threshold=80)
    assert len(clusters) == 1
    assert clusters[0]["suggested_canonical"] == "Refuse"


def test_detect_similar_categories_prefers_most_frequent_value_as_canonical():
    # Regression: a plain alphabetical-among-trimmed-members pick chose
    # "158an Francisco" (a single-occurrence OCR/typo corruption) over
    # "San Francisco" (tens of thousands of occurrences in the real
    # dataset this was found on) purely because a digit sorts before a
    # letter -- backwards. The dominant, most frequent spelling must win.
    values = (
        ["San Francisco"] * 100
        + ["158an Francisco", "San Frnacisco", "Sn Francisco"]
    )
    clusters = detect_similar_categories(values, threshold=80)
    assert len(clusters) == 1
    assert clusters[0]["suggested_canonical"] == "San Francisco"


def test_detect_similar_categories_frequency_tie_still_prefers_trimmed():
    # When frequency ties, the existing trimmed-member preference still
    # applies as the tiebreaker.
    values = ["Ada Lovelace", "Ada Lovelace", "  Ada Lovelace", "  Ada Lovelace"]
    clusters = detect_similar_categories(values)
    assert len(clusters) == 1
    assert clusters[0]["suggested_canonical"] == "Ada Lovelace"


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


def test_infer_semantic_role_distinguishes_poverty_ratio_from_raw_income():
    # Regression: "income_to_poverty_ratio" contains "income" as a
    # substring but is a normalized ratio, not a dollar figure -- lumping
    # it under the same "INCOME" role as wages/salary/earnings columns
    # made it look like a directly comparable dollar amount, which it
    # isn't. Two genuinely comparable raw income columns should still
    # share the INCOME role; only the ratio gets its own.
    assert infer_semantic_role("wages_income", {"category": "free_text"})["role"] == "INCOME"
    assert infer_semantic_role("total_personal_income", {"category": "free_text"})["role"] == "INCOME"
    assert infer_semantic_role("salary", {"category": "free_text"})["role"] == "INCOME"
    assert infer_semantic_role("income_to_poverty_ratio", {"category": "free_text"})["role"] == "INCOME_TO_POVERTY_RATIO"
    assert infer_semantic_role("poverty_ratio", {"category": "free_text"})["role"] == "INCOME_TO_POVERTY_RATIO"


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


def test_detect_duplicate_entities_empty_quasi_identifier_list_flags_nothing():
    # Regression test: grouping on zero columns groups every row into one
    # match by construction (an empty tuple key), which would otherwise
    # silently flag the entire dataset as one giant "duplicate entity."
    rows = [{"id": "1", "age": "10"}, {"id": "2", "age": "20"}, {"id": "3", "age": "30"}]
    assert detect_duplicate_entities(rows, [], "id") == []


def test_analyze_key_cardinality_one_to_one():
    child_values_all = ["P1", "P2", "P3"]
    result = analyze_key_cardinality(child_values_all, {"P1", "P2", "P3"})
    assert result["relationship"] == "one_to_one"
    assert result["max_children_per_parent"] == 1
    assert result["parents_with_multiple_children"] == 0


def test_analyze_key_cardinality_one_to_many():
    # P1 has 3 visit rows, P2 has 1 -- classic participants -> visits shape.
    child_values_all = ["P1", "P1", "P1", "P2"]
    result = analyze_key_cardinality(child_values_all, {"P1", "P2"})
    assert result["relationship"] == "one_to_many"
    assert result["max_children_per_parent"] == 3
    assert result["parents_with_multiple_children"] == 1


def test_analyze_key_cardinality_ignores_orphans():
    # "P9" isn't a real parent -- orphan analysis is check_referential_integrity's
    # job, cardinality should only count matched rows.
    child_values_all = ["P1", "P1", "P9"]
    result = analyze_key_cardinality(child_values_all, {"P1"})
    assert result["max_children_per_parent"] == 2
    assert result["relationship"] == "one_to_many"


def test_analyze_key_cardinality_no_matches():
    assert analyze_key_cardinality(["P9"], {"P1"})["relationship"] == "no_matches"


def test_compute_key_coverage_full_and_partial():
    full = compute_key_coverage({"P1", "P2"}, {"P1", "P2", "P3"})
    assert full["coverage_fraction"] == 1.0
    assert full["covered_count"] == 2

    partial = compute_key_coverage({"P1", "P2", "P3", "P4"}, {"P1", "P2", "P3"})
    assert partial["parent_count"] == 4
    assert partial["covered_count"] == 3
    assert partial["coverage_fraction"] == 0.75


def test_compute_key_coverage_empty_parents():
    assert compute_key_coverage(set(), {"P1"}) == {"parent_count": 0, "covered_count": 0, "coverage_fraction": 0.0}


def test_build_missingness_overview_includes_every_column_sorted_worst_first():
    dictionary = {
        "age": {"non_null_pct": 100.0},
        "bmi": {"non_null_pct": 91.6},
        "income": {"non_null_pct": 87.9},
    }
    overview = build_missingness_overview(dictionary)
    assert [row["column"] for row in overview] == ["income", "bmi", "age"]
    assert overview[0]["missing_pct"] == 12.1
    assert overview[-1]["missing_pct"] == 0.0


def test_detect_missingness_concentration_finds_age_gap():
    # BMI is missing specifically for the older participants (age ~80),
    # present for the younger ones (age ~30) -- a clear concentration.
    rows = []
    for i in range(10):
        rows.append({"bmi": "", "age": str(78 + i)})
    for i in range(10):
        rows.append({"bmi": "24.5", "age": str(28 + i)})
    results = detect_missingness_concentration(rows, "bmi", ["age"])
    assert len(results) == 1
    assert results[0]["column"] == "age"
    assert results[0]["direction"] == "higher"
    assert results[0]["missing_group_size"] == 10
    assert results[0]["present_group_size"] == 10


def test_detect_missingness_concentration_no_pattern_when_groups_similar():
    rows = []
    for i in range(10):
        rows.append({"bmi": "", "age": str(40 + (i % 5))})
    for i in range(10):
        rows.append({"bmi": "24.5", "age": str(40 + (i % 5))})
    assert detect_missingness_concentration(rows, "bmi", ["age"]) == []


def test_detect_missingness_concentration_requires_minimum_group_size():
    # Only 3 rows missing bmi -- below the minimum sample size to say
    # anything meaningful, even though the ages look very different.
    rows = [{"bmi": "", "age": "90"}, {"bmi": "", "age": "91"}, {"bmi": "", "age": "92"}]
    rows += [{"bmi": "24.5", "age": str(20 + i)} for i in range(10)]
    assert detect_missingness_concentration(rows, "bmi", ["age"]) == []


def test_detect_missingness_co_occurrence_finds_shared_gap():
    rows = []
    # 10 rows where income AND employment_status are both blank.
    for _ in range(10):
        rows.append({"income": "", "employment_status": "", "id": "x"})
    # 10 rows where both are present.
    for _ in range(10):
        rows.append({"income": "50000", "employment_status": "employed", "id": "x"})
    results = detect_missingness_co_occurrence(rows, ["income", "employment_status", "id"])
    assert len(results) == 1
    assert {results[0]["column_a"], results[0]["column_b"]} == {"income", "employment_status"}
    assert results[0]["both_missing_count"] == 10
    assert results[0]["overlap_fraction"] == 1.0


def test_detect_missingness_co_occurrence_no_pattern_for_independent_gaps():
    # income and employment_status are each missing on 10 DIFFERENT rows
    # (no overlap) -- independent gaps, not a shared cause.
    rows = []
    for i in range(20):
        rows.append(
            {
                "income": "" if i < 10 else "50000",
                "employment_status": "" if i >= 10 else "employed",
            }
        )
    assert detect_missingness_co_occurrence(rows, ["income", "employment_status"]) == []


def test_detect_value_shape_outliers_flags_minority_phone_format():
    rows = [{"phone": v} for v in ["555-100-0001", "555-100-0002", "555-100-0003", "555-100-0004", "5551000005"]]
    dictionary = {"phone": {"category": "free_text"}}
    results = detect_value_shape_outliers(rows, ["phone"], dictionary)
    assert results["phone"]["dominant_shape"] == "D-D-D"
    assert results["phone"]["dominant_count"] == 4
    assert results["phone"]["outlier_count"] == 1


def test_detect_value_shape_outliers_flags_name_with_embedded_digits():
    rows = [
        {"name": "John Smith"},
        {"name": "Jane Doe"},
        {"name": "Mary Jones"},
        {"name": "Bob123 Miller"},  # digits where a name shouldn't have any
        {"name": "Alice Brown"},
    ]
    dictionary = {"name": {"category": "free_text"}}
    results = detect_value_shape_outliers(rows, ["name"], dictionary)
    assert results["name"]["dominant_shape"] == "LSL"
    assert results["name"]["outlier_count"] == 1
    evidence = find_value_shape_outlier_evidence(rows, "name", results["name"]["dominant_shape"])
    assert evidence == [(3, "Bob123 Miller")]


def test_detect_value_shape_outliers_skips_genuine_free_text_with_no_dominant_shape():
    # Real notes -- every value has a different length/punctuation shape,
    # so no single shape reaches the 80% bar. Nothing defensible to flag.
    rows = [
        {"notes": "Patient reported mild fatigue."},
        {"notes": "No complaints at this visit"},
        {"notes": "Follow-up in 3 months, labs pending."},
        {"notes": "Referred to cardiology; see attached."},
        {"notes": "N/A"},
    ]
    dictionary = {"notes": {"category": "free_text"}}
    assert detect_value_shape_outliers(rows, ["notes"], dictionary) == {}


def test_detect_value_shape_outliers_skips_columns_below_minimum_size():
    rows = [{"phone": "555-100-0001"}, {"phone": "not-a-phone-number"}]
    dictionary = {"phone": {"category": "free_text"}}
    assert detect_value_shape_outliers(rows, ["phone"], dictionary) == {}


def test_detect_value_shape_outliers_skips_non_free_text_categories():
    # Same lopsided shapes as the phone test, but classified "categorical" --
    # this check is deliberately restricted to "free_text" columns.
    rows = [{"code": v} for v in ["A-1", "A-2", "A-3", "A-4", "99"]]
    dictionary = {"code": {"category": "categorical"}}
    assert detect_value_shape_outliers(rows, ["code"], dictionary) == {}


def test_detect_value_shape_outliers_no_finding_when_fully_consistent():
    rows = [{"phone": v} for v in ["555-100-0001", "555-100-0002", "555-100-0003", "555-100-0004", "555-100-0005"]]
    dictionary = {"phone": {"category": "free_text"}}
    assert detect_value_shape_outliers(rows, ["phone"], dictionary) == {}


def test_find_value_shape_outlier_evidence_ignores_blank_values():
    rows = [
        {"phone": "555-100-0001"},
        {"phone": ""},
        {"phone": "5551000002"},
    ]
    evidence = find_value_shape_outlier_evidence(rows, "phone", "D-D-D")
    assert evidence == [(2, "5551000002")]


def test_detect_whitespace_anomalies_flags_leading_trailing_and_doubled_spaces():
    rows = [
        {"city": "California"},
        {"city": " California"},
        {"city": "California "},
        {"city": "New  York"},
        {"city": "Texas"},
    ]
    counts = detect_whitespace_anomalies(rows, ["city"])
    assert counts == {"city": 3}
    evidence = find_whitespace_anomaly_evidence(rows, "city")
    assert evidence == [(1, " California"), (2, "California "), (3, "New  York")]


def test_detect_whitespace_anomalies_ignores_clean_values():
    rows = [{"city": "California"}, {"city": "Texas"}]
    assert detect_whitespace_anomalies(rows, ["city"]) == {}


def test_detect_invisible_characters_flags_zero_width_and_nbsp():
    # Built via explicit \u escapes, not typed literals -- a zero-width
    # space and a non-breaking space are visually indistinguishable from a
    # normal space (or nothing at all) in an editor, so typing them
    # directly risks silently testing the wrong character entirely.
    zwsp_name = "Jane" + "\u200b" + "Doe"
    nbsp_name = "Bob" + "\xa0" + "Miller"
    rows = [
        {"name": "John Smith"},
        {"name": zwsp_name},
        {"name": nbsp_name},
    ]
    counts = detect_invisible_characters(rows, ["name"])
    assert counts == {"name": 2}
    evidence = find_invisible_character_evidence(rows, "name")
    assert evidence == [(1, zwsp_name), (2, nbsp_name)]


def test_detect_invisible_characters_does_not_flag_legitimate_tab_or_newline():
    rows = [{"note": "line one\nline two"}, {"note": "col1\tcol2"}]
    assert detect_invisible_characters(rows, ["note"]) == {}


def test_detect_encoding_corruption_flags_real_mojibake():
    # Derive the corrupted string the same way it actually happens in the
    # wild -- encode as UTF-8, then decode those bytes as if they were
    # Windows-1252 -- rather than hand-typing a mojibake literal, which is
    # too easy to get subtly wrong.
    corrupted = "José".encode().decode("cp1252")
    rows = [{"name": "John Smith"}, {"name": corrupted}]
    counts = detect_encoding_corruption(rows, ["name"])
    assert counts == {"name": 1}
    evidence = find_encoding_corruption_evidence(rows, "name")
    assert evidence == [(1, corrupted)]


def test_detect_encoding_corruption_ignores_clean_ascii_and_clean_unicode():
    rows = [{"name": "John Smith"}, {"name": "José"}]
    assert detect_encoding_corruption(rows, ["name"]) == {}


def test_detect_numeric_representation_inconsistency_flags_currency_and_suffix():
    rows = [{"income": str(v)} for v in range(10000, 10050, 5)]  # 10 clean numbers
    rows += [{"income": "$50,000"}, {"income": "50k"}]
    dictionary = {"income": {"category": "free_text"}}
    result = detect_numeric_representation_inconsistency(rows, ["income"], dictionary)
    assert result["income"]["clean_count"] == 10
    assert result["income"]["decorated_count"] == 2
    evidence = find_numeric_representation_evidence(rows, "income")
    assert evidence == [(10, "$50,000"), (11, "50k")]


def test_detect_numeric_representation_inconsistency_skips_when_decorated_form_is_the_norm():
    # Every value is "$"-prefixed -- there's no plain-number convention
    # for these to be inconsistent with.
    rows = [{"income": f"${v}"} for v in range(10000, 10050, 5)]
    dictionary = {"income": {"category": "free_text"}}
    assert detect_numeric_representation_inconsistency(rows, ["income"], dictionary) == {}


def test_detect_age_columns_that_look_like_years():
    rows = [{"age": str(v)} for v in [1978, 1990, 2001, 1985, 1999]]
    role_by_column = {"age": {"role": "AGE"}}
    result = detect_age_columns_that_look_like_years(rows, role_by_column)
    assert result["age"]["year_like_count"] == 5
    evidence = find_year_like_value_evidence(rows, "age")
    assert len(evidence) == 5


def test_detect_age_columns_that_look_like_years_ignores_genuine_ages():
    rows = [{"age": str(v)} for v in [25, 30, 45, 62, 8]]
    role_by_column = {"age": {"role": "AGE"}}
    assert detect_age_columns_that_look_like_years(rows, role_by_column) == {}


def test_detect_age_columns_that_look_like_years_ignores_non_age_roles():
    rows = [{"year_built": str(v)} for v in [1978, 1990, 2001, 1985, 1999]]
    role_by_column = {"year_built": {"role": "DATE"}}
    assert detect_age_columns_that_look_like_years(rows, role_by_column) == {}


def test_find_ordered_column_pairs_matches_start_end_and_min_max():
    columns = ["start_date", "end_date", "min_reading", "max_reading", "unrelated_col"]
    pairs = find_ordered_column_pairs(columns)
    assert ("start_date", "end_date") in pairs
    assert ("min_reading", "max_reading") in pairs
    assert len(pairs) == 2


def test_detect_column_order_violations_flags_start_after_end_date():
    rows = [
        {"start_date": "2024-01-01", "end_date": "2024-01-10"},
        {"start_date": "2024-02-15", "end_date": "2024-02-01"},  # violation
    ]
    result = detect_column_order_violations(rows, ["start_date", "end_date"])
    assert result[("start_date", "end_date")] == [(1, "2024-02-15", "2024-02-01")]


def test_detect_column_order_violations_flags_numeric_min_above_max():
    rows = [
        {"min_reading": "10", "max_reading": "20"},
        {"min_reading": "30", "max_reading": "25"},  # violation
    ]
    result = detect_column_order_violations(rows, ["min_reading", "max_reading"])
    assert result[("min_reading", "max_reading")] == [(1, "30", "25")]


def test_find_column_order_violation_evidence_skips_ambiguous_or_blank_rows():
    rows = [
        {"start_date": "", "end_date": "2024-01-10"},
        {"start_date": "not-a-date", "end_date": "also-not-a-date"},
    ]
    assert find_column_order_violation_evidence(rows, "start_date", "end_date") == []


def test_detect_unit_inconsistency_flags_kg_lb_mix():
    kg_values = [60, 65, 70, 75, 80]
    lb_values = [v * 2.20462 for v in kg_values]
    rows = [{"weight": str(v)} for v in kg_values + lb_values]
    role_by_column = {"weight": {"role": "WEIGHT"}}
    result = detect_unit_inconsistency(rows, role_by_column)
    assert "weight" in result
    f = result["weight"]
    assert f["unit_a"] == "kg" and f["unit_b"] == "lb"
    assert abs(f["observed_ratio"] - 2.20462) < 0.01
    evidence = find_unit_inconsistency_evidence(rows, "weight", f["boundary"], f["minority_side"])
    assert len(evidence) == 5


def test_detect_unit_inconsistency_flags_cm_in_mix():
    cm_values = [150, 160, 170, 180, 190]
    in_values = [v / 2.54 for v in cm_values]
    rows = [{"height": str(v)} for v in cm_values + in_values]
    role_by_column = {"height": {"role": "HEIGHT"}}
    result = detect_unit_inconsistency(rows, role_by_column)
    assert "height" in result
    assert result["height"]["unit_a"] == "cm" and result["height"]["unit_b"] == "in"


def test_detect_unit_inconsistency_flags_dollars_vs_thousands():
    dollars_values = [45000, 50000, 55000, 60000, 65000]
    thousands_values = [v / 1000 for v in dollars_values]
    rows = [{"income": str(v)} for v in dollars_values + thousands_values]
    role_by_column = {"income": {"role": "INCOME"}}
    result = detect_unit_inconsistency(rows, role_by_column)
    assert "income" in result
    assert result["income"]["unit_a"] == "dollars"


def test_detect_unit_inconsistency_does_not_flag_genuine_bimodal_subpopulation():
    # Child and adult weights are both genuinely "weight" -- a real
    # bimodal split, but the cluster ratio (~2.9) doesn't match any known
    # unit-conversion factor, so this must NOT be flagged as a units
    # problem. This is the core false-positive guard for this check.
    child_weights = [20, 22, 24, 26, 28]
    adult_weights = [60, 65, 70, 75, 80]
    rows = [{"weight": str(v)} for v in child_weights + adult_weights]
    role_by_column = {"weight": {"role": "WEIGHT"}}
    assert detect_unit_inconsistency(rows, role_by_column) == {}


def test_detect_unit_inconsistency_no_finding_on_consistent_column():
    rows = [{"weight": str(v)} for v in [65, 68, 70, 72, 75, 78, 80, 82]]
    role_by_column = {"weight": {"role": "WEIGHT"}}
    assert detect_unit_inconsistency(rows, role_by_column) == {}


def test_detect_unit_inconsistency_ignores_non_matching_roles():
    rows = [{"age": str(v)} for v in [20, 22, 24, 60, 65, 70]]
    role_by_column = {"age": {"role": "AGE"}}
    assert detect_unit_inconsistency(rows, role_by_column) == {}


def test_detect_unit_inconsistency_ignores_columns_with_too_few_values():
    kg_values = [60, 65]
    lb_values = [v * 2.20462 for v in kg_values]
    rows = [{"weight": str(v)} for v in kg_values + lb_values]
    role_by_column = {"weight": {"role": "WEIGHT"}}
    assert detect_unit_inconsistency(rows, role_by_column) == {}


def test_find_unit_inconsistency_evidence_returns_only_minority_cluster():
    kg_values = [60, 62, 64, 66, 68, 70]  # majority group (6)
    lb_values = [v * 2.20462 for v in [60, 65, 70]]  # minority group (3)
    rows = [{"weight": str(v)} for v in kg_values + lb_values]
    role_by_column = {"weight": {"role": "WEIGHT"}}
    result = detect_unit_inconsistency(rows, role_by_column)
    f = result["weight"]
    assert f["minority_side"] == "high"
    evidence = find_unit_inconsistency_evidence(rows, "weight", f["boundary"], f["minority_side"])
    assert len(evidence) == 3
