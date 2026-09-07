import pytest

from dataforensics.harmonize import (
    HarmonizeSafetyError,
    assert_row_and_column_integrity,
    compute_safety_report,
    merge_files_on_key,
)


def test_row_count_mismatch_raises():
    input_rows = [{"a": "1"}, {"a": "2"}]
    output_rows = [{"a": "1"}]  # a row silently disappeared
    with pytest.raises(HarmonizeSafetyError, match="row"):
        assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="exact")


def test_exact_column_mismatch_raises():
    input_rows = [{"a": "1", "b": "2"}]
    output_rows = [{"a": "1"}]  # column "b" silently disappeared
    with pytest.raises(HarmonizeSafetyError, match="column"):
        assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="exact")


def test_exact_column_match_passes():
    input_rows = [{"a": "1", "b": "2"}]
    output_rows = [{"a": "1", "b": "99"}]  # value changed, columns unchanged
    assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="exact")


def test_column_count_mismatch_raises():
    # simulates two distinct source columns colliding onto one target name
    # during crosswalk remapping -- a silent column loss even though
    # column_map is a legitimate rename mechanism.
    input_rows = [{"county_fips": "1", "PUMA": "2"}]
    output_rows = [{"geography_fips": "1"}]  # both columns collapsed into one
    with pytest.raises(HarmonizeSafetyError, match="column"):
        assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="count")


def test_column_count_match_passes_even_with_renamed_columns():
    # crosswalk column_map legitimately renames columns -- names may differ
    # as long as the count survives.
    input_rows = [{"county_fips": "1", "age_group": "25-34"}]
    output_rows = [{"geography_fips": "1", "age_band": "25-34"}]
    assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="count")


def test_empty_input_rows_skips_column_check():
    assert_row_and_column_integrity([], [], context="test", columns="exact")


def test_input_columns_override_anchors_to_true_file_header():
    # Simulates the exact regression this safety net exists to catch: a
    # duplicate-header file (e.g. "pid,sex,sex") whose *rows* have already
    # been silently dict-collapsed by an upstream parse (read_rows) before
    # this check ever runs. If the column check only compares two views
    # already derived from that same corrupted parse (rows[0].keys() on
    # both sides), it passes trivially -- the input_columns override lets
    # the caller anchor the *input* side to the true on-disk header (3
    # names) instead, so the mismatch against the already-collapsed output
    # (2 names) is still caught.
    collapsed_rows = [{"pid": "1", "sex": "F"}]  # what read_rows produced post-collapse
    with pytest.raises(HarmonizeSafetyError, match="column"):
        assert_row_and_column_integrity(
            collapsed_rows,
            collapsed_rows,
            context="test",
            columns="exact",
            input_columns=["pid", "sex", "sex"],  # true file header, duplicate intact
        )


def test_input_columns_override_not_given_falls_back_to_input_rows():
    input_rows = [{"a": "1", "b": "2"}]
    output_rows = [{"a": "1", "b": "99"}]
    assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="exact")


def test_column_check_catches_per_row_drift_not_just_row_zero():
    # Row 0 looks fine on both sides, but row 1's output row picked up an
    # extra '' key (e.g. from a trailing-delimiter line) that row 0 doesn't
    # have. A row[0]-only check would pass this trivially and the mismatch
    # would only surface later as an uncaught csv.DictWriter ValueError.
    input_rows = [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
    output_rows = [{"a": "1", "b": "2"}, {"a": "3", "b": "4", "": ""}]
    with pytest.raises(HarmonizeSafetyError, match="column"):
        assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="exact")


def test_column_check_union_passes_when_all_rows_consistent():
    input_rows = [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
    output_rows = [{"a": "1", "b": "99"}, {"a": "3", "b": "88"}]
    assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="exact")


def test_input_row_count_override_anchors_to_true_file_row_count():
    # Mirrors test_input_columns_override_anchors_to_true_file_header, but
    # for the row-count check: simulates a regression *inside* the shared
    # upstream parse (e.g. strip_footer dropping a genuine data line)
    # that shrinks both input_rows and output_rows identically before this
    # check ever runs. If the row check only compares len(input_rows) to
    # len(output_rows), it passes trivially even though a real on-disk row
    # went missing. The input_row_count override lets the caller anchor the
    # *input* side to the true on-disk row count (4) instead, so the
    # mismatch against the already-shrunk output (3 rows) is still caught.
    shrunk_rows = [{"a": "1"}, {"a": "2"}, {"a": "3"}]  # 4th row silently dropped upstream
    with pytest.raises(HarmonizeSafetyError, match="row"):
        assert_row_and_column_integrity(
            shrunk_rows,
            shrunk_rows,
            context="test",
            columns="exact",
            input_row_count=4,  # true on-disk row count
        )


def test_input_row_count_override_not_given_falls_back_to_len_input_rows():
    input_rows = [{"a": "1"}, {"a": "2"}]
    output_rows = [{"a": "1"}, {"a": "2"}]
    assert_row_and_column_integrity(input_rows, output_rows, context="test", columns="exact")


def test_input_row_count_override_passes_when_output_matches_true_count():
    # Sanity check: passing the true anchor count doesn't spuriously fail
    # when nothing was actually dropped.
    input_rows = [{"a": "1"}, {"a": "2"}]
    output_rows = [{"a": "1"}, {"a": "2"}]
    assert_row_and_column_integrity(
        input_rows, output_rows, context="test", columns="exact", input_row_count=2
    )


def test_column_union_detects_drift_missed_by_row_zero_alone():
    from dataforensics.harmonize import column_union

    # row 0 has no "" key; row 1 does (e.g. a ragged/trailing-delimiter row) --
    # checking only rows[0] would miss this, exactly the bug this exists to catch.
    rows = [{"id": "1", "age": "30"}, {"id": "2", "age": "40", "": "extra"}]
    assert column_union(rows) == ["id", "age", ""]


def test_column_union_preserves_first_seen_order():
    from dataforensics.harmonize import column_union

    rows = [{"b": "1", "a": "2"}, {"c": "3"}]
    assert column_union(rows) == ["b", "a", "c"]


def test_column_union_empty_rows():
    from dataforensics.harmonize import column_union

    assert column_union([]) == []


def test_compute_safety_report_all_passed_when_only_values_change():
    input_rows = [{"id": "1", "status": "99"}, {"id": "2", "status": "10"}]
    output_rows = [{"id": "1", "status": "Refused"}, {"id": "2", "status": "10"}]
    report = compute_safety_report(input_rows, output_rows, primary_key=["id"])
    assert report["all_passed"] is True
    assert report["row_count"] == {"before": 2, "after": 2, "passed": True}
    assert report["column_count"] == {"before": 2, "after": 2, "passed": True}
    assert report["primary_key_uniqueness"] == {"before": 2, "after": 2, "passed": True}
    assert report["modified_columns"] == ["status"]
    assert report["unmodified_columns"] == ["id"]


def test_compute_safety_report_flags_row_count_mismatch():
    input_rows = [{"id": "1"}, {"id": "2"}]
    output_rows = [{"id": "1"}]
    report = compute_safety_report(input_rows, output_rows, primary_key=["id"])
    assert report["row_count"]["passed"] is False
    assert report["all_passed"] is False


def test_compute_safety_report_flags_column_count_mismatch():
    input_rows = [{"id": "1", "age": "30"}]
    output_rows = [{"id": "1"}]
    report = compute_safety_report(input_rows, output_rows, primary_key=["id"])
    assert report["column_count"]["passed"] is False
    assert report["all_passed"] is False


def test_compute_safety_report_flags_primary_key_collision():
    # Two originally-distinct ids that would collapse onto the same value
    # -- an approved mapping accidentally merging two real records.
    input_rows = [{"id": "1"}, {"id": "2"}]
    output_rows = [{"id": "1"}, {"id": "1"}]
    report = compute_safety_report(input_rows, output_rows, primary_key=["id"])
    assert report["primary_key_uniqueness"] == {"before": 2, "after": 1, "passed": False}
    assert report["all_passed"] is False


def test_compute_safety_report_no_columns_modified():
    rows = [{"id": "1", "age": "30"}, {"id": "2", "age": "40"}]
    report = compute_safety_report(rows, rows, primary_key=["id"])
    assert report["modified_columns"] == []
    assert set(report["unmodified_columns"]) == {"id", "age"}


def test_merge_files_on_key_left_join_keeps_every_file_b_row():
    file_a = [{"pid": "1", "sex": "F"}, {"pid": "2", "sex": "M"}]
    file_b = [
        {"pid": "1", "visit_date": "2024-01-01"},
        {"pid": "1", "visit_date": "2024-06-01"},
        {"pid": "3", "visit_date": "2024-02-01"},  # no match in file_a
    ]
    result = merge_files_on_key(file_a, file_b, "pid", "pid", "participants.csv", "visits.csv", join_type="left")
    assert result["total_count"] == 3
    assert result["matched_count"] == 2
    assert result["unmatched_count"] == 1
    assert set(result["columns"]) == {"pid", "visit_date", "sex"}
    # unmatched row keeps its own fields, blank for the unmatched side
    unmatched_row = next(r for r in result["rows"] if r["pid"] == "3")
    assert unmatched_row["sex"] == ""


def test_merge_files_on_key_inner_join_drops_unmatched():
    file_a = [{"pid": "1", "sex": "F"}]
    file_b = [{"pid": "1", "visit_date": "2024-01-01"}, {"pid": "2", "visit_date": "2024-02-01"}]
    result = merge_files_on_key(file_a, file_b, "pid", "pid", "a.csv", "b.csv", join_type="inner")
    assert result["total_count"] == 1
    assert result["matched_count"] == 1
    assert result["unmatched_count"] == 1  # counted even though excluded from the output
    assert result["rows"][0]["pid"] == "1"


def test_merge_files_on_key_suffixes_shared_non_key_columns():
    file_a = [{"pid": "1", "sex": "F"}]
    file_b = [{"pid": "1", "sex": "M"}]  # disagrees with file_a -- must never silently overwrite
    result = merge_files_on_key(file_a, file_b, "pid", "pid", "participants.csv", "visits.csv")
    assert "sex (participants.csv)" in result["columns"]
    assert "sex (visits.csv)" in result["columns"]
    assert "sex" not in result["columns"]
    assert result["rows"][0]["sex (participants.csv)"] == "F"
    assert result["rows"][0]["sex (visits.csv)"] == "M"


def test_merge_files_on_key_uses_first_occurrence_for_duplicate_parent_key():
    file_a = [{"pid": "1", "sex": "F"}, {"pid": "1", "sex": "M"}]  # duplicate key, first wins
    file_b = [{"pid": "1", "visit_date": "2024-01-01"}]
    result = merge_files_on_key(file_a, file_b, "pid", "pid", "a.csv", "b.csv")
    assert result["rows"][0]["sex"] == "F"


def test_merge_files_on_key_never_mutates_inputs():
    file_a = [{"pid": "1", "sex": "F"}]
    file_b = [{"pid": "1", "visit_date": "2024-01-01"}]
    file_a_copy = [dict(r) for r in file_a]
    file_b_copy = [dict(r) for r in file_b]
    merge_files_on_key(file_a, file_b, "pid", "pid", "a.csv", "b.csv")
    assert file_a == file_a_copy
    assert file_b == file_b_copy
