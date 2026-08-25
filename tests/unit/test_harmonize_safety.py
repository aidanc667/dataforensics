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
