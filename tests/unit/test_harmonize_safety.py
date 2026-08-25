import pytest

from rdh.harmonize import HarmonizeSafetyError, assert_row_and_column_integrity


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
    from rdh.harmonize import column_union

    # row 0 has no "" key; row 1 does (e.g. a ragged/trailing-delimiter row) --
    # checking only rows[0] would miss this, exactly the bug this exists to catch.
    rows = [{"id": "1", "age": "30"}, {"id": "2", "age": "40", "": "extra"}]
    assert column_union(rows) == ["id", "age", ""]


def test_column_union_preserves_first_seen_order():
    from rdh.harmonize import column_union

    rows = [{"b": "1", "a": "2"}, {"c": "3"}]
    assert column_union(rows) == ["b", "a", "c"]


def test_column_union_empty_rows():
    from rdh.harmonize import column_union

    assert column_union([]) == []
