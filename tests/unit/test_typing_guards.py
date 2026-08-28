from dataforensics.typing_guards import (
    classify_sentinel,
    is_id_like_column,
    is_pii_like_column,
    parse_finite_float,
    preserves_leading_zero,
)


def test_id_like_column_names():
    assert is_id_like_column("participant_id") is True
    assert is_id_like_column("county_fips") is True
    assert is_id_like_column("geoid") is True
    assert is_id_like_column("zip_code") is True
    assert is_id_like_column("age") is False
    assert is_id_like_column("income") is False


def test_preserves_leading_zero_detects_fips():
    assert preserves_leading_zero(["06081", "02138", "48201"]) is True


def test_preserves_leading_zero_false_for_no_zero_padding():
    assert preserves_leading_zero(["120", "45", "9001"]) is False


def test_classify_sentinel_returns_label():
    sentinel_map = {"99": "Refused", "-9": "Not applicable"}
    assert classify_sentinel("99", sentinel_map) == "Refused"
    assert classify_sentinel("-9", sentinel_map) == "Not applicable"


def test_classify_sentinel_none_for_ordinary_value():
    sentinel_map = {"99": "Refused"}
    assert classify_sentinel("42", sentinel_map) is None


def test_id_like_column_rejects_substring_traps():
    """Verify that partial matches of keywords don't trigger false positives."""
    assert is_id_like_column("residence") is False
    assert is_id_like_column("solid") is False
    assert is_id_like_column("valid") is False
    assert is_id_like_column("avoid") is False
    assert is_id_like_column("unzip") is False
    assert is_id_like_column("rapid") is False


def test_id_like_column_matches_census_geo_columns():
    """Verify recognition of Census FIPS/GEOID columns without underscore boundaries."""
    assert is_id_like_column("COUNTYFP") is True
    assert is_id_like_column("STATEFP") is True
    assert is_id_like_column("GEOID10") is True
    assert is_id_like_column("GEOID20") is True
    assert is_id_like_column("PUMA") is True


def test_id_like_column_matches_puma_vintage_variants():
    assert is_id_like_column("PUMA00") is True
    assert is_id_like_column("PUMA10") is True
    assert is_id_like_column("MIGPUMA") is True
    assert is_id_like_column("MIGPUMA00") is True
    assert is_id_like_column("MIGPUMA10") is True


def test_pii_like_column_matches_spelled_out_forms():
    """Spelled-out equivalents of the abbreviated PII tokens must also match,
    not just dob/ssn/phone."""
    assert is_pii_like_column("date_of_birth") is True
    assert is_pii_like_column("birthdate") is True
    assert is_pii_like_column("birth_date") is True
    assert is_pii_like_column("social_security_number") is True
    assert is_pii_like_column("social_security") is True
    assert is_pii_like_column("telephone_number") is True
    assert is_pii_like_column("telephone") is True


def test_pii_like_column_name_matching_unaffected_by_phone_dob_ssn_loosening():
    """Loosening the phone/dob/ssn matching must not reopen the false-positive
    traps that the "name" matching logic was specifically built to avoid."""
    assert is_pii_like_column("county_name") is False
    assert is_pii_like_column("site_name") is False
    assert is_pii_like_column("test_name") is False


def test_parse_finite_float_parses_ordinary_numbers():
    assert parse_finite_float("42") == 42.0
    assert parse_finite_float("-3.14") == -3.14
    assert parse_finite_float("0") == 0.0


def test_parse_finite_float_rejects_nan():
    # Python's bare float() happily accepts "nan" and returns a value that
    # compares False against everything -- a "nan" cell would silently
    # pass a configured minimum/maximum check undetected, and corrupt
    # sort-based statistics (min/max/median/quartiles) if it slipped into
    # a values list. Must be rejected, not silently parsed.
    assert parse_finite_float("nan") is None
    assert parse_finite_float("NaN") is None
    assert parse_finite_float("NAN") is None


def test_parse_finite_float_rejects_infinity():
    assert parse_finite_float("inf") is None
    assert parse_finite_float("-inf") is None
    assert parse_finite_float("Infinity") is None


def test_parse_finite_float_rejects_non_numeric_text():
    assert parse_finite_float("not a number") is None
    assert parse_finite_float("") is None


def test_parse_finite_float_rejects_non_string_types_gracefully():
    assert parse_finite_float(None) is None
    assert parse_finite_float(["1", "2"]) is None
