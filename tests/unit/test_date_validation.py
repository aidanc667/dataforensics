from dataforensics.validation import is_ambiguous_date, validate

_DATE_RULES = {
    "version": 1,
    "primary_key": ["participant_id"],
    "columns": {
        "visit_date": {"type": "date"},
    },
    "missing_values": {},
    "category_mappings": {},
    "weights_strata": {"columns": []},
}

_DATE_RULES_WITH_FORMAT = {
    **_DATE_RULES,
    "columns": {"visit_date": {"type": "date", "format": "%Y-%m-%d"}},
}


def test_is_ambiguous_date_flags_slash_format():
    assert is_ambiguous_date("03/04/2024") is True


def test_is_ambiguous_date_does_not_flag_iso8601():
    assert is_ambiguous_date("2024-03-04") is False


def test_slash_date_with_no_declared_format_is_error():
    rows = [{"participant_id": "1", "visit_date": "03/04/2024"}]
    result = validate(rows, _DATE_RULES)
    ambiguous = [e for e in result["errors"] if e["rule"] == "ambiguous_date_format"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["column"] == "visit_date"


def test_iso8601_date_with_no_declared_format_is_not_flagged():
    rows = [{"participant_id": "1", "visit_date": "2024-03-04"}]
    result = validate(rows, _DATE_RULES)
    assert result["errors"] == []


def test_value_matching_declared_format_is_not_flagged():
    rows = [{"participant_id": "1", "visit_date": "2024-03-04"}]
    result = validate(rows, _DATE_RULES_WITH_FORMAT)
    assert result["errors"] == []


def test_value_not_matching_declared_format_is_error():
    rows = [{"participant_id": "1", "visit_date": "03/04/2024"}]
    result = validate(rows, _DATE_RULES_WITH_FORMAT)
    mismatches = [e for e in result["errors"] if e["rule"] == "date_format_mismatch"]
    assert len(mismatches) == 1
