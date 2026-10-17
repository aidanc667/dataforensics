from dataforensics.advanced_checks import (
    detect_coordinate_problems,
    detect_datetime_issues,
    detect_excel_date_serials,
    detect_free_text_personal_details,
    detect_impossible_negatives,
    detect_longitudinal_inconsistencies,
    detect_placeholder_text,
    detect_rare_combinations,
    detect_scientific_notation_ids,
    detect_temperature_unit_mix,
    detect_truncated_text,
    detect_zip_state_mismatch,
    excel_serial_to_iso,
    find_free_text_personal_detail_rows,
    find_lost_leading_zero_columns,
    merge_synonym_clusters,
    synonym_clusters,
    zip_to_state,
)

CAT = {"category": "categorical"}


# 1. Spreadsheet damage ---------------------------------------------------
def test_scientific_notation_ids():
    rows = [{"record_id": v} for v in ["123456789012", "123456789013", "1.23457E+11", "123456789015"]]
    assert detect_scientific_notation_ids(rows, ["record_id"], {}) == {"record_id": [(2, "1.23457E+11")]}
    # A measurement column with one sci-notation value isn't an ID.
    rows = [{"conc": v} for v in ["0.5", "1.2", "3E+2"]]
    assert detect_scientific_notation_ids(rows, ["conc"], {}) == {}


def test_excel_serials():
    # 45000 days after 1899-12-30 is 2023-03-15 (Excel's 1900 date system).
    assert excel_serial_to_iso("45000") == "2023-03-15"
    assert excel_serial_to_iso("45000.5") is None
    assert excel_serial_to_iso("12") is None
    rows = [{"d": v} for v in ["2023-01-02", "2023-01-05", "45000", "2023-02-01"]]
    assert detect_excel_date_serials(rows, ["d"]) == {"d": [(2, "45000", "2023-03-15")]}
    # Not a date column: numbers stay numbers.
    assert detect_excel_date_serials([{"n": "45000"}, {"n": "45100"}], ["n"]) == {}


def test_lost_leading_zeros_in_ids():
    rows = [{"patient_id": v} for v in ["00123", "00456", "01789", "12345", "123", "00999"]]
    assert find_lost_leading_zero_columns(rows, ["patient_id"], set()) == {"patient_id": {"width": 5, "count": 1}}
    # No code starts with 0 -> nothing proves zeros matter here.
    rows = [{"patient_id": v} for v in ["12345", "23456", "34567", "45678", "123"]]
    assert find_lost_leading_zero_columns(rows, ["patient_id"], set()) == {}


# 2. Synonyms --------------------------------------------------------------
def test_synonym_clusters_for_sex_codes():
    values = ["M"] * 5 + ["Male"] * 10 + ["male"] * 2 + ["F"] * 4 + ["Female"] * 9
    clusters = {c["suggested_canonical"]: c for c in synonym_clusters(values)}
    assert clusters["Male"]["values"] == ["M", "Male", "male"]
    assert clusters["Female"]["values"] == ["F", "Female"]
    assert all(c["synonym"] and c["confidence"] == "high" for c in clusters.values())


def test_f_means_false_in_a_true_false_column():
    clusters = synonym_clusters(["T"] * 5 + ["F"] * 5 + ["True"] * 3 + ["False"] * 3)
    assert {c["suggested_canonical"]: c["values"] for c in clusters} == {"Yes": ["T", "True"], "No": ["F", "False"]}


def test_synonyms_need_a_dominant_family_and_two_spellings():
    assert synonym_clusters(["Male", "Female", "Other", "Other", "Unknown"]) == []  # 40% coverage
    assert synonym_clusters(["Yes", "No", "Yes"]) == []  # one spelling each


def test_synonym_cluster_replaces_overlapping_fuzzy_cluster():
    fuzzy = [{"values": ["Male", "male"], "suggested_canonical": "Male"}, {"values": ["Red", "red"], "suggested_canonical": "Red"}]
    syn = [{"values": ["M", "Male", "male"], "suggested_canonical": "Male", "synonym": True}]
    merged = merge_synonym_clusters(fuzzy, syn)
    assert [c["values"] for c in merged] == [["Red", "red"], ["M", "Male", "male"]]


# 3. Negatives ---------------------------------------------------------------
def test_impossible_negatives_skip_codes_and_signed_names():
    rows = [{"age": "-3", "visit_count": "-1", "weight_change": "-4", "age2": "-99"}, {"age": "-99", "visit_count": "2", "weight_change": "1", "age2": "5"}]
    result = detect_impossible_negatives(rows, ["age", "visit_count", "weight_change"], exclude={"age": {"-99"}})
    assert result == {"age": [(0, "-3")], "visit_count": [(0, "-1")]}


# 4. Longitudinal --------------------------------------------------------------
def test_age_going_down_and_sex_changing_for_the_same_person():
    rows = [
        {"subject_id": "A", "visit_date": "2020-01-01", "age": "40", "sex": "F"},
        {"subject_id": "A", "visit_date": "2021-01-01", "age": "38", "sex": "F"},
        {"subject_id": "B", "visit_date": "2020-01-01", "age": "50", "sex": "M"},
        {"subject_id": "B", "visit_date": "2021-01-01", "age": "51", "sex": "F"},
        {"subject_id": "C", "visit_date": "2020-05-01", "age": "30", "sex": "M"},
    ]
    roles = {"age": {"role": "AGE"}, "sex": {"role": "SEX_OR_GENDER"}, "visit_date": {"role": "DATE"}, "subject_id": None}
    dictionary = {"subject_id": {"category": "id", "unique_count": 3}}
    result = detect_longitudinal_inconsistencies(rows, list(rows[0]), dictionary, roles)
    assert result["entity"] == "subject_id" and result["date"] == "visit_date"
    assert result["age_decreases"] == [("A", [0, 1])]
    assert result["changing"] == {"sex": [("B", ["F", "M"])]}


def test_one_row_per_entity_is_not_longitudinal():
    rows = [{"id": str(i), "d": "2020-01-01"} for i in range(5)]
    assert detect_longitudinal_inconsistencies(rows, ["id", "d"], {"id": {"category": "id", "unique_count": 5}}, {}) == {}


# 5. Location ------------------------------------------------------------------
def test_zip_to_state_prefixes():
    assert zip_to_state("80302") == "CO"
    assert zip_to_state("10001") == "NY"
    assert zip_to_state("02139") == "MA"
    assert zip_to_state("73301") == "TX"  # 733 is Texas inside Oklahoma's range
    assert zip_to_state("abc") is None


def test_zip_state_mismatch_accepts_names_and_codes():
    rows = [
        {"zip": "80302", "state": "CO"},
        {"zip": "80302", "state": "Colorado"},
        {"zip": "10001", "state": "CA"},
        {"zip": "", "state": "CA"},
    ]
    result = detect_zip_state_mismatch(rows, ["zip", "state"])
    assert result["evidence"] == [(2, "10001", "CA", "NY")]


def test_coordinates_out_of_range_and_swapped():
    rows = [{"lat": "39.7", "lon": "-104.9"}, {"lat": "-104.9", "lon": "39.7"}, {"lat": "95", "lon": "200"}]
    result = detect_coordinate_problems(rows, ["lat", "lon"])
    # Row 1 is reported once, as swapped -- not also as an out-of-range latitude.
    assert [i for i, _ in result["lat/lon swapped"]] == [1]
    assert result["lat"] == [(2, "95")]
    assert result["lon"] == [(2, "200")]
    # "long_term_care" is not a longitude column.
    assert detect_coordinate_problems([{"lat": "40", "long_term_care": "500"}], ["lat", "long_term_care"]) == {}


# 6. Temperature ------------------------------------------------------------------
def test_fahrenheit_values_in_a_celsius_column():
    celsius = ["36.6", "37.0", "36.8", "37.2", "36.9", "37.1", "36.7", "37.3"]
    fahrenheit = ["98.6", "99.1", "98.2"]  # 37.0, 37.3, 36.8 C
    rows = [{"temperature": v} for v in celsius + fahrenheit]
    result = detect_temperature_unit_mix(rows, ["temperature"])
    assert result["temperature"]["fahrenheit_count"] == 3
    assert [v for _, v in result["temperature"]["evidence"]] == ["98.6", "99.1", "98.2"]


def test_a_plain_celsius_column_is_not_flagged():
    rows = [{"temp": str(v)} for v in [10, 12, 15, 18, 20, 22, 25, 28]]
    assert detect_temperature_unit_mix(rows, ["temp"]) == {}


# 7. Date-times ----------------------------------------------------------------------
def test_mixed_time_zones_and_impossible_times():
    rows = [{"ts": v} for v in ["2024-01-05T10:00:00Z", "2024-01-05 11:00", "2024-01-05T12:00:00+05:00", "2024-01-05 25:10"]]
    result = detect_datetime_issues(rows, ["ts"])["ts"]
    assert result["with_zone"] == 2 and result["without_zone"] == 2
    assert result["offsets"] == ["+0000", "+0500"]
    assert result["invalid"] == [(3, "2024-01-05 25:10")]


def test_consistent_utc_timestamps_are_fine():
    rows = [{"ts": f"2024-01-0{d}T10:00:00Z"} for d in range(1, 6)]
    assert detect_datetime_issues(rows, ["ts"]) == {}


# 8. Personal details in free text ------------------------------------------------------
def test_addresses_and_titled_names_in_notes_never_return_the_text():
    rows = [{"notes": "Lives at 12 Elm Street, call later"}, {"notes": "Referred by Dr. Patel"}, {"notes": "fine"}]
    assert detect_free_text_personal_details(rows, ["notes"], {}) == {"notes": {"street address": 1, "titled name": 1}}
    assert find_free_text_personal_detail_rows(rows, "notes") == [(0, "street address"), (1, "titled name")]
    # A column already named as personal data is masked elsewhere, not double-reported.
    assert detect_free_text_personal_details([{"address": "12 Elm Street"}], ["address"], {}) == {}


# 9. Rare combinations ---------------------------------------------------------------
def test_rare_combination_of_common_values():
    rows = [{"sex": "Female", "pregnant": "Yes"}] * 20 + [{"sex": "Female", "pregnant": "No"}] * 20
    rows += [{"sex": "Male", "pregnant": "No"}] * 39 + [{"sex": "Male", "pregnant": "Yes"}]
    dictionary = {"sex": {"category": "categorical", "unique_count": 2}, "pregnant": {"category": "categorical", "unique_count": 2}}
    (finding,) = detect_rare_combinations(rows, ["sex", "pregnant"], dictionary)
    assert finding["values"] == ("Male", "Yes")
    assert finding["count"] == 1
    # Expected under independence: 40 male x 21 yes / 80 = 10.5.
    assert finding["expected"] == 10.5
    assert finding["rows"] == [79]


# 10. Free-text quality -------------------------------------------------------------------
def test_placeholder_text():
    rows = [{"comment": v} for v in ["good visit", "asdf", "TBD", "aaaaaaa", "follow up", "seen", "ok then"]]
    assert detect_placeholder_text(rows, ["comment"], {}) == {"comment": [(1, "asdf"), (2, "TBD"), (3, "aaaaaaa")]}


def test_truncated_text_at_a_field_limit():
    rows = [{"t": "x" * 255} for _ in range(4)] + [{"t": f"short note {i}"} for i in range(16)]
    result = detect_truncated_text(rows, ["t"])
    assert result == {"t": {"limit": 255, "count": 4, "rows": [0, 1, 2, 3]}}
    rows = [{"t": "y" * 200} for _ in range(4)] + [{"t": "note"} for _ in range(16)]
    assert detect_truncated_text(rows, ["t"]) == {}


def test_synonym_spellings_are_not_a_change_over_time():
    rows = [
        {"subject_id": "A", "visit_date": "2020-01-01", "sex": "M"},
        {"subject_id": "A", "visit_date": "2021-01-01", "sex": "Male"},
        {"subject_id": "A", "visit_date": "2022-01-01", "sex": " male "},
    ]
    roles = {"sex": {"role": "SEX_OR_GENDER"}, "visit_date": {"role": "DATE"}, "subject_id": None}
    result = detect_longitudinal_inconsistencies(rows, list(rows[0]), {"subject_id": {"category": "id", "unique_count": 1}}, roles)
    assert result["changing"] == {}


def test_valid_iso_end_of_day_and_leap_second_are_not_impossible():
    rows = [{"ts": v} for v in ["2024-01-05T24:00:00Z", "2016-12-31T23:59:60Z", "2024-01-05T10:00:00Z", "2024-01-05T24:30:00Z"]]
    result = detect_datetime_issues(rows, ["ts"])
    assert result["ts"]["invalid"] == [(3, "2024-01-05T24:30:00Z")]


def test_real_words_are_not_placeholders():
    rows = [{"split": v} for v in ["train", "test", "validation", "sample", "bar", "ABC", "holdout"]]
    assert detect_placeholder_text(rows, ["split"], {}) == {}
