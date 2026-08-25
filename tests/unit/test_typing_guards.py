from rdh.typing_guards import (
    classify_sentinel,
    is_id_like_column,
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
