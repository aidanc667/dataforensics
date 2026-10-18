import pytest

from dataforensics.cleaning import (
    blank_only_where,
    PII_MASK_PLACEHOLDER,
    apply_safe_fixes,
    check_judgment_fixes,
    clean_dataset,
    drop_duplicate_rows,
    find_leading_zero_candidates,
    fix_date_iso,
    fix_invisible,
    fix_leading_zeros,
    fix_mojibake,
    fix_numeric_format,
    fix_outliers,
    fix_pii_mask,
    fix_units,
    fix_whitespace,
    impute_fill_value,
    is_fill_candidate,
    looks_like_codes,
    outlier_fix_params,
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


# --------------------------------------------------------------------- #
# Judgment-call fixes: outliers, personal-data masking, filling gaps
# --------------------------------------------------------------------- #
_CAP = {"action": "cap", "lower": 10.0, "upper": 50.0, "keep": ["9999"]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("75", "50"),      # above the upper limit -> capped to it
        ("-3", "10"),      # below the lower limit -> capped to it
        ("10", "10"),      # on a limit: unchanged
        ("32.5", "32.5"),  # inside: unchanged
        ("9999", "9999"),  # a missing-value code is never turned into a number
        ("n/a", "n/a"),    # not a number: unchanged
    ],
)
def test_fix_outliers_caps_to_the_limits(raw, expected):
    assert fix_outliers(raw, **_CAP) == expected


def test_fix_outliers_blank_empties_only_out_of_range_values():
    blank = {**_CAP, "action": "blank"}
    assert fix_outliers("75", **blank) == ""
    assert fix_outliers("20", **blank) == "20"
    assert fix_outliers("9999", **blank) == "9999"


def test_outlier_fix_params_uses_the_checks_iqr_limits():
    # Q1 = 20, Q3 = 40, IQR = 20 -> limits 20 - 30 = -10 and 40 + 30 = 70.
    params = outlier_fix_params({"q1": 20.0, "q3": 40.0, "iqr": 20.0}, ["9999", "-99"])
    assert params == {"action": "cap", "lower": -10.0, "upper": 70.0, "keep": ["-99", "9999"]}


def test_fix_pii_mask_replaces_only_the_personal_data():
    text = "call 555-123-4567 or mail ann@example.com; SSN 123-45-6789"
    assert fix_pii_mask(text) == "call [phone removed] or mail [email removed]; SSN [SSN removed]"
    assert fix_pii_mask("routine intake") == "routine intake"
    assert fix_pii_mask(fix_pii_mask(text)) == fix_pii_mask(text)  # idempotent


def test_impute_fill_value_median_mode_and_codes():
    rows = [{"w": v} for v in ["10", "20", "30", "40", "", "9999"]]
    # 9999 is a missing-value code, not a weight: median of 10, 20, 30, 40.
    assert impute_fill_value(rows, "w", {"9999"}) == ("median", "25")
    cats = [{"s": v} for v in ["b", "a", "b", "a", "c", ""]]
    assert impute_fill_value(cats, "s") == ("most common value", "a")  # tie a/b -> alphabetical
    assert impute_fill_value([{"x": ""}, {"x": " "}], "x") is None


def test_apply_impute_fills_only_empty_cells_and_logs_each():
    rows = [{"id": "1", "w": "10"}, {"id": "2", "w": ""}, {"id": "3", "w": None}]
    out, mutations = apply_safe_fixes(rows, {"impute": {"w": "25"}}, ["id"], reason="test")
    assert [r["w"] for r in out] == ["10", "25", "25"]
    assert [(m["row_key"]["id"], m["original_value"], m["new_value"], m["transformation_rule"]) for m in mutations] == [
        ("2", "", "25", "impute:w"), ("3", "", "25", "impute:w"),
    ]
    # Re-running changes nothing.
    assert apply_safe_fixes(out, {"impute": {"w": "25"}}, ["id"], reason="test")[1] == []


def test_blanked_outlier_is_then_filled_and_the_result_is_stable():
    fixes = {"outliers": {"w": {**_CAP, "action": "blank"}}, "impute": {"w": "25"}}
    rows = [{"id": "1", "w": "75"}, {"id": "2", "w": "20"}, {"id": "3", "w": "9999"}]
    out, mutations = apply_safe_fixes(rows, fixes, ["id"], reason="test")
    assert [r["w"] for r in out] == ["25", "20", "9999"]
    assert [m["transformation_rule"] for m in mutations] == ["outliers:w", "impute:w"]
    assert apply_safe_fixes(out, fixes, ["id"], reason="test")[1] == []


def test_pii_mask_never_logs_the_personal_data():
    rows = [{"id": "1", "notes": "reach me at ann@example.com"}]
    out, mutations = apply_safe_fixes(rows, {"pii_mask": ["notes"]}, ["id"], reason="test")
    assert out[0]["notes"] == "reach me at [email removed]"
    (m,) = mutations
    assert m["original_value"] == PII_MASK_PLACEHOLDER
    assert "ann@example.com" not in str(m)


@pytest.mark.parametrize(
    ("fixes", "message"),
    [
        ({"outliers": {"w": {"action": "drop", "lower": 1, "upper": 2, "keep": []}}}, "cap' or 'blank"),
        ({"outliers": {"w": {"action": "cap", "lower": 5, "upper": 2, "keep": []}}}, "greater than upper"),
        ({"outliers": {"w": {"action": "cap", "lower": 1}}}, "needs exactly"),
        ({"impute": {"w": ""}}, "non-empty string"),
        ({"impute": ["w"]}, "mapping"),
        ({"pii_mask": "notes"}, "list of column names"),
    ],
)
def test_validate_rejects_malformed_judgment_fixes(fixes, message):
    with pytest.raises(ValueError, match=message):
        validate_fixes(fixes)


def test_fill_is_offered_for_numbers_and_real_categories_only():
    assert is_fill_candidate("numeric", 50, 50)
    assert is_fill_candidate("categorical", 40, 4)       # values repeat: a real category
    assert not is_fill_candidate("categorical", 12, 9)   # mostly one-off text (notes, names)
    assert not is_fill_candidate("date", 40, 3)
    assert not is_fill_candidate("numeric", 0, 0)


def test_fill_uses_the_most_common_value_for_numeric_category_codes():
    # Employment status codes 1-8: a "median" of 3.5 would be no answer at all.
    codes = [{"emp": v} for v in ["1", "1", "7", "7", "7", "2", "8", ""]]
    assert impute_fill_value(codes, "emp") == ("most common value", "7")
    # Real measurements with many distinct values still get the median.
    weights = [{"w": str(v)} for v in range(100, 130)]
    assert impute_fill_value(weights, "w") == ("median", "114.5")


def test_personal_data_never_reaches_the_log_through_an_earlier_fix():
    # Regression (review): trimming spaces runs before masking and logged the
    # untrimmed text -- phone number and all -- in the audit trail.
    rows = [{"id": "1", "notes": "  call 555-123-4567"}]
    out, mutations = apply_safe_fixes(rows, {"whitespace": ["notes"], "pii_mask": ["notes"]}, ["id"], reason="t")
    assert out[0]["notes"] == "call [phone removed]"
    assert "555-123-4567" not in str(mutations)
    # The same through clean_dataset, where a mapping on the column runs first.
    rules = {"primary_key": ["id"], "category_mappings": {"notes": {"email ann@example.com": "email bob@example.com"}}}
    result = clean_dataset([{"id": "1", "notes": "email ann@example.com"}], rules, {"pii_mask": ["notes"]}, [], ["id"],
                           reason="t")
    assert result["rows"][0]["notes"] == "email [email removed]"
    assert "@example.com" not in str(result["mutations"])


@pytest.mark.parametrize(
    ("rules", "fixes", "message"),
    [
        # A fill an approved merge maps away would change again on re-cleaning.
        ({"category_mappings": {"sex": {"M": "Male"}}}, {"impute": {"sex": "M"}}, "mapped away"),
        ({"missing_values": {"w": {"9999": ""}}}, {"impute": {"w": "9999"}}, "mapped away"),
        # A fill must not write personal data into every gap.
        ({}, {"impute": {"contact": "info@clinic.org"}}, "personal data"),
        # Never on the record ID.
        ({}, {"impute": {"id": "7"}}, "primary key"),
        ({}, {"outliers": {"id": {"action": "cap", "lower": 1, "upper": 9, "keep": []}}}, "primary key"),
    ],
)
def test_check_judgment_fixes_refuses_self_defeating_fixes(rules, fixes, message):
    with pytest.raises(ValueError, match=message):
        check_judgment_fixes(rules, fixes, ["id"])


def test_cap_is_exact_and_whole_number_columns_get_whole_limits():
    # A limit with many decimals is used exactly (a rounded cap fell outside it).
    assert fix_outliers("5", action="cap", lower=0.0, upper=1.23456789, keep=[]) == "1.23456789"
    # Ages: Q1 30, Q3 51, IQR 21 -> limits -1.5 and 82.5, rounded outward to -2 and 83.
    assert outlier_fix_params({"q1": 30, "q3": 51, "iqr": 21}, [], whole_numbers=True)["upper"] == 83.0
    assert outlier_fix_params({"q1": 30, "q3": 51, "iqr": 21}, [], whole_numbers=True)["lower"] == -2.0


def test_fill_treats_columns_named_as_codes_as_categories():
    rows = [{"county_code": str(v)} for v in range(100, 140)] + [{"county_code": "105"}]
    assert impute_fill_value(rows, "county_code", as_category=True) == ("most common value", "105")


def test_looks_like_codes():
    assert looks_like_codes(["1", "2", "2", "5", "5"])               # rating scale
    # Regression: 13 income-bracket codes (1-11, 77, 99) on a big survey.
    brackets = [str(c) for c in [*range(1, 12), 77, 99]] * 40
    assert looks_like_codes(brackets)
    assert not looks_like_codes(["10", "20", "30", "40"])           # one-off whole numbers
    assert not looks_like_codes([str(w) for w in range(100, 160)])   # many distinct weights
    assert not looks_like_codes(["1.5", "2", "2"])                  # not whole numbers


def test_blank_only_where_finds_not_applicable_blanks():
    # Wages blank for exactly the ages 0-14, never for 15+: not asked, so a
    # fill would give a child a salary (the ACS sample's real pattern).
    ages = [(i * 37) % 60 for i in range(60)]  # every age 0-59 once, not in file order
    rows = [{"age": str(a), "wages": "" if a < 15 else str(1000 * a)} for a in ages]
    assert blank_only_where(rows, "wages", ["age"]) == ("age", 0.0, 14.0)


def test_blank_only_where_ignores_blanks_spread_across_the_range():
    rows = [{"age": str(a), "wages": "" if a % 7 == 0 else "100"} for a in ((i * 37) % 60 for i in range(60))]
    assert blank_only_where(rows, "wages", ["age"]) is None


def test_blank_only_where_needs_five_rows_on_each_side():
    rows = [{"age": str(a), "wages": "" if a < 3 else "100"} for a in ((i * 37) % 60 for i in range(60))]
    assert blank_only_where(rows, "wages", ["age"]) is None


def test_audit_names_the_row_when_the_key_repeats():
    # Regression (accuracy audit): ACS's default key (household_id) repeats
    # within a household, so "household_id=H1" couldn't say whose cell changed.
    rows = [{"hh": "H1", "p": "1", "x": " a"}, {"hh": "H1", "p": "2", "x": " b"}]
    rules = {"version": 1, "primary_key": ["hh"], "columns": {}, "missing_values": {}, "category_mappings": {}}
    result = clean_dataset(rows, rules, {"whitespace": ["x"]}, [], ["hh"], reason="test")
    assert [m["row_key"] for m in result["mutations"]] == [{"hh": "H1", "row": 1}, {"hh": "H1", "row": 2}]


def test_audit_key_has_no_row_number_when_the_key_is_unique():
    rows = [{"hh": "H1", "x": " a"}, {"hh": "H2", "x": " b"}]
    rules = {"version": 1, "primary_key": ["hh"], "columns": {}, "missing_values": {}, "category_mappings": {}}
    result = clean_dataset(rows, rules, {"whitespace": ["x"]}, [], ["hh"], reason="test")
    assert [m["row_key"] for m in result["mutations"]] == [{"hh": "H1"}, {"hh": "H2"}]


def test_blank_only_where_ignores_a_row_order_column():
    # A measure that stopped being recorded for the last records: blanks sit
    # in one range of the row sequence, but that's not "not applicable".
    rows = [{"seq": str(i), "bp": "" if i >= 40 else "120"} for i in range(50)]
    assert blank_only_where(rows, "bp", ["seq"]) is None


def test_blank_only_where_still_works_on_a_file_sorted_by_age():
    # Age rises down the file but repeats -- not an index -- so the
    # "not applicable" range is still recognised.
    ages = sorted((i * 37) % 60 for i in range(120))
    rows = [{"age": str(a), "wages": "" if a < 15 else "100"} for a in ages]
    assert blank_only_where(rows, "wages", ["age"]) == ("age", 0.0, 14.0)
