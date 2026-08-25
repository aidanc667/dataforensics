from rdh.investigate import (
    detect_ambiguous_date_columns,
    detect_candidate_sentinels,
    detect_duplicate_rows,
    detect_similar_categories,
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
