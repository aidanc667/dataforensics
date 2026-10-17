import pytest

from dataforensics.cleaning import (
    PII_MASK_PLACEHOLDER,
    apply_safe_fixes,
    drop_duplicate_rows,
    find_leading_zero_candidates,
    fix_date_iso,
    fix_invisible,
    fix_leading_zeros,
    fix_mojibake,
    fix_numeric_format,
    fix_units,
    fix_whitespace,
    validate_fixes,
)
from dataforensics.investigate import detect_duplicate_rows


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" California ", "California"),
        ("New  York", "New York"),
        ("Main   St  ", "Main St"),
        ("tab\t\there", "tab here"),
        ("clean", "clean"),
    ],
)
def test_fix_whitespace(raw, expected):
    assert fix_whitespace(raw) == expected


def test_fix_invisible_removes_zero_width_and_converts_nbsp():
    assert fix_invisible("Den​ver") == "Denver"
    assert fix_invisible("New\xa0York") == "New York"
    assert fix_invisible("﻿id") == "id"
    # Tabs/newlines are legitimate inside quoted text and are kept.
    assert fix_invisible("a\tb\nc") == "a\tb\nc"


def test_fix_mojibake_repairs_only_corrupted_text():
    assert fix_mojibake("JosÃ©") == "José"
    assert fix_mojibake("José") == "José"
    assert fix_mojibake("plain") == "plain"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$50,000", "50000"),
        ("50k", "50000"),
        ("1.2M", "1200000"),
        ("€1,234.50", "1234.5"),
        ("-2,000", "-2000"),
        ("45%", "45%"),  # ambiguous scale: deliberately untouched
        ("50000", "50000"),  # already plain
        ("n/a", "n/a"),
    ],
)
def test_fix_numeric_format(raw, expected):
    assert fix_numeric_format(raw) == expected


def test_fix_leading_zeros():
    assert fix_leading_zeros("8001", 5) == "08001"
    assert fix_leading_zeros("08001", 5) == "08001"
    assert fix_leading_zeros("ABC", 5) == "ABC"


def test_fix_date_iso_uses_declared_format_and_leaves_unparseable():
    assert fix_date_iso("03/04/2021", "%d/%m/%Y") == "2021-04-03"
    assert fix_date_iso("03/04/2021", "%m/%d/%Y") == "2021-03-04"
    assert fix_date_iso("31/31/2021", "%d/%m/%Y") == "31/31/2021"
    assert fix_date_iso("03-04-2021", "%d/%m/%Y") == "2021-04-03"


def test_fix_units_converts_only_the_minority_cluster():
    # kg column (majority, low) with some pound values (minority, high).
    params = {"boundary": 100.0, "minority_side": "high", "factor": 2.20462, "minority_min": 110.0, "minority_max": 250.0}
    assert fix_units("154.32", **params) == "70"  # 154.32 / 2.20462 = 69.998 -> 70.0
    assert fix_units("70", **params) == "70"
    assert fix_units("abc", **params) == "abc"
    low = {"boundary": 100.0, "minority_side": "low", "factor": 2.20462, "minority_min": 40.0, "minority_max": 100.0}
    assert fix_units("70", **low) == "154.32"  # 70 * 2.20462 = 154.3234


def test_apply_safe_fixes_logs_each_change_and_preserves_rows():
    rows = [
        {"id": "1", "city": " Denver ", "income": "$50,000", "zip": "8001"},
        {"id": "2", "city": "Boulder", "income": "40000", "zip": "80302"},
        {"id": "3", "city": "", "income": "", "zip": ""},
    ]
    fixes = {"whitespace": ["city"], "numeric_format": ["income"], "leading_zeros": {"zip": 5}}
    out, mutations = apply_safe_fixes(rows, fixes, ["id"], reason="Approved")

    assert out == [
        {"id": "1", "city": "Denver", "income": "50000", "zip": "08001"},
        {"id": "2", "city": "Boulder", "income": "40000", "zip": "80302"},
        {"id": "3", "city": "", "income": "", "zip": ""},
    ]
    assert rows[0]["city"] == " Denver "  # input not mutated
    assert [(m["column"], m["original_value"], m["new_value"], m["transformation_rule"]) for m in mutations] == [
        ("city", " Denver ", "Denver", "whitespace:city"),
        ("income", "$50,000", "50000", "numeric_format:income"),
        ("zip", "8001", "08001", "leading_zeros:zip"),
    ]
    assert all(m["row_key"] == {"id": "1"} for m in mutations)


def test_apply_safe_fixes_is_idempotent():
    rows = [{"id": "1", "name": " José​  Smith", "w": "154.32"}]
    fixes = {"invisible": ["name"], "whitespace": ["name"], "units": {"w": {"boundary": 100.0, "minority_side": "high", "factor": 2.20462, "minority_min": 110.0, "minority_max": 250.0}}}
    once, _ = apply_safe_fixes(rows, fixes, ["id"], reason="r")
    twice, second_mutations = apply_safe_fixes(once, fixes, ["id"], reason="r")
    assert once == twice
    assert second_mutations == []


def test_apply_safe_fixes_masks_pii_like_columns():
    rows = [{"id": "1", "email": " a@b.com "}]
    _, mutations = apply_safe_fixes(rows, {"whitespace": ["email"]}, ["id"], reason="r")
    assert mutations[0]["original_value"] == PII_MASK_PLACEHOLDER
    assert mutations[0]["new_value"] == PII_MASK_PLACEHOLDER


@pytest.mark.parametrize(
    "fixes",
    [
        {"nope": ["a"]},
        {"whitespace": "a"},
        {"leading_zeros": {"zip": 0}},
        {"leading_zeros": {"zip": True}},
        {"date_iso": {"d": "DD/MM"}},
        {"units": {"w": {"boundary": 1, "minority_side": "middle", "factor": 2, "minority_min": 1, "minority_max": 2}}},
        {"units": {"w": {"boundary": 1, "minority_side": "high", "factor": 2, "minority_min": 5, "minority_max": 2}}},
        {"units": {"w": {"boundary": True, "minority_side": "high", "factor": 2, "minority_min": 1, "minority_max": 2}}},
        {"units": {"w": {"boundary": 1, "minority_side": "low"}}},
        {"drop_exact_duplicate_rows": "yes"},
        {"leading_zeros": ["zip"]},
        {"date_iso": None},
    ],
)
def test_validate_fixes_rejects_malformed(fixes):
    with pytest.raises(ValueError):
        validate_fixes(fixes)


def test_drop_duplicate_rows_removes_flagged_and_logs():
    rows = [{"id": "1", "x": "a"}, {"id": "2", "x": "b"}, {"id": "1", "x": "a"}]
    dups = detect_duplicate_rows(rows)
    kept, mutations = drop_duplicate_rows(rows, dups, ["id"], reason="r")
    assert kept == [{"id": "1", "x": "a"}, {"id": "2", "x": "b"}]
    assert len(mutations) == 1
    assert mutations[0]["original_value"] == "data row 3"
    assert "duplicate of data row 1" in mutations[0]["new_value"]


def test_drop_duplicate_rows_refuses_rows_that_no_longer_match():
    rows = [{"id": "1", "x": "a"}, {"id": "1", "x": "CHANGED"}]
    with pytest.raises(ValueError, match="no longer identical"):
        drop_duplicate_rows(rows, [{"row_index": 1, "duplicate_of_row_index": 0}], ["id"], reason="r")


def test_find_leading_zero_candidates():
    rows = [
        {"zip": "8001", "state_fips": "9", "county_fips": "8001", "other": "12"},
        {"zip": "80302", "state_fips": "36", "county_fips": "36061", "other": "1"},
    ]
    assert find_leading_zero_candidates(rows, ["zip", "state_fips", "county_fips", "other"]) == {
        "zip": {"width": 5, "count": 1},
        "state_fips": {"width": 2, "count": 1},
        "county_fips": {"width": 5, "count": 1},
    }


def test_units_fix_never_converts_a_missing_value_code_or_typo():
    # Regression: a 9999 code (or a 1500 typo) on the minority side of the
    # boundary was converted like a pound value -> 4535.4, a plausible-
    # looking weight, and again to 2057.3 on a second run.
    from dataforensics.investigate import detect_unit_inconsistency

    kg = [60, 62, 65, 68, 70, 72, 75, 78, 80, 82]
    lb = [round(v * 2.20462, 1) for v in (61, 66, 71, 76)]
    rows = [{"w": str(v)} for v in kg + lb] + [{"w": "9999"}, {"w": "1500"}]
    finding = detect_unit_inconsistency(rows, {"w": {"role": "WEIGHT"}}, exclude={"w": {"9999"}})["w"]
    assert finding["separable"] is True
    assert finding["minority_max"] < 1500

    from dataforensics.cleaning import unit_fix_params

    fixes = {"units": {"w": unit_fix_params(finding)}}
    once, _ = apply_safe_fixes(rows, fixes, [], reason="r")
    assert once[-2]["w"] == "9999"
    assert once[-1]["w"] == "1500"
    assert once[len(kg)]["w"] == "61.01"  # 134.5 lb / 2.20462 = 61.008 kg
    twice, second = apply_safe_fixes(once, fixes, [], reason="r")
    assert twice == once and second == []


def test_units_fix_reapplied_to_cleaned_output_is_a_no_op():
    from dataforensics.cleaning import unit_fix_params
    from dataforensics.investigate import detect_unit_inconsistency

    kg = [60, 62, 65, 68, 70, 72, 75, 78, 80, 82]
    rows = [{"w": str(v)} for v in kg] + [{"w": v} for v in ("134.5", "145.5", "156.5", "300")]
    finding = detect_unit_inconsistency(rows, {"w": {"role": "WEIGHT"}})["w"]
    fixes = {"units": {"w": unit_fix_params(finding)}}
    once, _ = apply_safe_fixes(rows, fixes, [], reason="r")
    twice, second = apply_safe_fixes(once, fixes, [], reason="r")
    assert second == [] and twice == once


def test_clean_dataset_refuses_conflicting_mappings():
    from dataforensics.cleaning import clean_dataset

    rows = [{"id": "1", "s": "N/A"}]
    rules = {"primary_key": ["id"], "missing_values": {"s": {"N/A": ""}}, "category_mappings": {"s": {"N/A": "n/a"}}}
    with pytest.raises(ValueError, match="conflict"):
        clean_dataset(rows, rules, {}, [], ["id"], reason="r")
    rules = {"primary_key": ["id"], "missing_values": {}, "category_mappings": {"s": {"a": "b", "b": "c"}}}
    with pytest.raises(ValueError, match="chain"):
        clean_dataset(rows, rules, {}, [], ["id"], reason="r")


def test_mojibake_that_repairs_into_a_hidden_character_is_fully_cleaned():
    # "Annâ€‹" repairs to "Ann" + zero-width space; with encoding repair now
    # first, the hidden-character fix removes it in the same pass.
    rows = [{"id": "1", "n": "Annâ€‹"}]
    fixes = {"mojibake": ["n"], "invisible": ["n"], "whitespace": ["n"]}
    once, _ = apply_safe_fixes(rows, fixes, ["id"], reason="r")
    assert once[0]["n"] == "Ann"
    assert apply_safe_fixes(once, fixes, ["id"], reason="r")[1] == []


def test_excel_date_fix_converts_only_serials():
    from dataforensics.cleaning import fix_excel_date

    assert fix_excel_date("45000") == "2023-03-15"
    assert fix_excel_date("2023-03-15") == "2023-03-15"
    assert fix_excel_date("12") == "12"
    rows = [{"id": "1", "d": "45000"}, {"id": "2", "d": "2023-01-02"}]
    out, mutations = apply_safe_fixes(rows, {"excel_dates": ["d"]}, ["id"], reason="r")
    assert [r["d"] for r in out] == ["2023-03-15", "2023-01-02"]
    assert [m["transformation_rule"] for m in mutations] == ["excel_dates:d"]
