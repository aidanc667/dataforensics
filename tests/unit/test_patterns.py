from dataforensics.patterns import (
    age_dob_check_applies,
    detect_age_dob_mismatch,
    detect_group_missingness,
    detect_heaping,
    detect_rare_categories,
    detect_redundant_columns,
    detect_total_mismatch,
    find_total_candidates,
)

FREE = {"category": "free_text"}
CAT = {"category": "categorical"}


def test_heaping_flags_round_number_preference():
    # 60 values: 40 multiples of 5 (67%), 20 others -- far above the 20% expected.
    values = [40, 45, 50, 55, 60] * 8 + [41, 43, 47, 52, 58] * 4
    rows = [{"age": str(v)} for v in values]
    result = detect_heaping(rows, ["age"], {"age": FREE})
    assert result["age"]["heaped_count"] == 40
    assert result["age"]["eligible_count"] == 60
    assert round(result["age"]["share"], 3) == 0.667
    assert result["age"]["index"] == 333  # 0.667 / 0.2 * 100


def test_heaping_ignores_uniform_digits_and_excluded_codes():
    rows = [{"w": str(v)} for v in range(100, 200)]  # every final digit equally often -> 20%
    assert detect_heaping(rows, ["w"], {"w": FREE}) == {}
    # 9999 codes would otherwise not count (9999 % 5 != 0), but 9995 would; exclusion keeps codes out.
    rows = [{"w": str(v)} for v in range(100, 160)] + [{"w": "9995"}] * 40
    assert detect_heaping(rows, ["w"], {"w": FREE}, exclude={"w": {"9995"}}) == {}


def test_heaping_measured_at_column_resolution():
    # Incomes recorded to the nearest $1,000 with no preference among
    # thousands digits: every value is a multiple of 5 in units, but the
    # thousands digit is uniform -> no heaping.
    rows = [{"inc": str(k * 1000)} for k in range(10, 110)]
    assert detect_heaping(rows, ["inc"], {"inc": FREE}) == {}
    # Same resolution, but people round to 50k/60k/...: flagged, reported in real units.
    rows = [{"inc": str(k * 1000)} for k in [50, 60, 70, 80, 100] * 12 + list(range(11, 31))]
    result = detect_heaping(rows, ["inc"], {"inc": FREE})
    assert result["inc"]["resolution"] == 1000
    assert result["inc"]["top_values"][0][0] in {"50000", "60000", "70000", "80000", "100000"}


def test_heaping_skips_small_samples_and_categorical_columns():
    rows = [{"x": "50"}] * 30
    assert detect_heaping(rows, ["x"], {"x": FREE}) == {}  # < 50 values
    rows = [{"x": "50"}] * 80 + [{"x": "10"}] * 20
    assert detect_heaping(rows, ["x"], {"x": CAT}) == {}


def test_group_missingness_by_category_reports_rates():
    # income missing for 15/30 of group A (50%) vs 3/30 of group B (10%).
    rows = (
        [{"income": "", "grp": "A"}] * 15
        + [{"income": "100", "grp": "A"}] * 15
        + [{"income": "", "grp": "B"}] * 3
        + [{"income": "100", "grp": "B"}] * 27
    )
    result = detect_group_missingness(rows, ["income"], ["grp"], [])
    assert len(result) == 1
    r = result[0]
    assert r["highest"] == {"group": "grp = A", "rate": 0.5, "n": 30}
    assert r["lowest"] == {"group": "grp = B", "rate": 0.1, "n": 30}
    assert round(r["gap"], 2) == 0.4


def test_group_missingness_by_numeric_split_and_sentinel_counts_as_missing():
    # Older half (age >= 60) has income code 9999 on 20/25 rows; younger half 0/25.
    rows = [{"age": str(30 + i), "income": "500"} for i in range(25)]
    rows += [{"age": str(60 + i), "income": "9999" if i < 20 else "500"} for i in range(25)]
    result = detect_group_missingness(rows, ["income"], [], ["age"], exclude={"income": {"9999"}})
    assert len(result) == 1
    assert result[0]["kind"] == "numeric_split"
    assert result[0]["highest"]["group"].startswith("age ≥")
    assert result[0]["highest"]["rate"] == 0.8
    assert result[0]["lowest"]["rate"] == 0.0


def test_group_missingness_ignores_chance_gaps_in_small_groups():
    # 9/20 (45%) vs 3/20 (15%): a 30-point gap and 3x ratio, but with 20
    # rows per group z = (0.45-0.15)/sqrt(0.3*0.7*(2/20)) = 2.07 < 3.
    rows = (
        [{"y": "", "g": "A"}] * 9 + [{"y": "1", "g": "A"}] * 11
        + [{"y": "", "g": "B"}] * 3 + [{"y": "1", "g": "B"}] * 17
    )
    assert detect_group_missingness(rows, ["y"], ["g"], []) == []


def test_group_missingness_ignores_small_gaps_and_small_groups():
    rows = [{"y": "" if i % 10 == 0 else "1", "g": "A" if i < 50 else "B"} for i in range(100)]
    assert detect_group_missingness(rows, ["y"], ["g"], []) == []  # 10% vs 10%
    rows = [{"y": "", "g": "A"}] * 10 + [{"y": "1", "g": "B"}] * 30
    assert detect_group_missingness(rows, ["y"], ["g"], []) == []  # group A has < 20 rows


def test_redundant_columns_identical_and_recoded():
    rows = [
        {"code": "1", "label": "Male", "copy": "1", "other": "x"},
        {"code": "2", "label": "Female", "copy": "2", "other": "x"},
        {"code": "1", "label": "Male", "copy": "1", "other": "y"},
        {"code": "2", "label": "Female", "copy": "2", "other": "x"},
    ]
    dictionary = {c: {"unique_count": 2} for c in ["code", "label", "copy", "other"]}
    groups = detect_redundant_columns(rows, ["code", "label", "copy", "other"], dictionary)
    assert groups == [{"columns": ["code", "label", "copy"], "relationship": "one-to-one recoding"}]
    groups = detect_redundant_columns(rows, ["code", "copy"], dictionary)
    assert groups == [{"columns": ["code", "copy"], "relationship": "identical"}]


def test_redundant_columns_skips_near_unique_columns():
    # 20 rows, 19 distinct names/dates: coincidentally one-to-one, not redundant.
    rows = [{"name": f"n{i % 19}", "dob": f"d{i % 19}"} for i in range(20)]
    dictionary = {"name": {"unique_count": 19}, "dob": {"unique_count": 19}}
    assert detect_redundant_columns(rows, ["name", "dob"], dictionary) == []


def test_redundant_columns_skips_all_unique_and_constant():
    rows = [{"a": str(i), "b": f"x{i}", "c": "k"} for i in range(5)]
    dictionary = {"a": {"unique_count": 5}, "b": {"unique_count": 5}, "c": {"unique_count": 1}}
    assert detect_redundant_columns(rows, ["a", "b", "c"], dictionary) == []


def test_rare_categories():
    rows = [{"state": "CO"}] * 60 + [{"state": "WY"}] * 40 + [{"state": "Colorad"}] + [{"state": "-99"}]
    result = detect_rare_categories(rows, ["state"], {"state": CAT}, exclude={"state": {"-99"}})
    assert result == {"state": [("Colorad", 1)]}


def test_age_dob_mismatch_uses_reference_date():
    roles = {"age": {"role": "AGE"}, "dob": {"role": "DATE_OF_BIRTH"}, "visit_date": {"role": "DATE"}}
    rows = [
        {"age": "38", "dob": "1985-03-04", "visit_date": "2024-01-10"},  # 38 completed years: OK
        {"age": "52", "dob": "1985-03-04", "visit_date": "2024-01-10"},  # off by 14
        {"age": "39", "dob": "1985-03-04", "visit_date": "2024-01-10"},  # off by 1: within tolerance
        {"age": "40", "dob": "03/04/1985", "visit_date": "2024-01-10"},  # non-ISO: skipped
    ]
    assert age_dob_check_applies(roles)
    assert detect_age_dob_mismatch(rows, roles) == {("age", "dob", "visit_date"): [(1, "52", 38)]}
    assert not age_dob_check_applies({"age": {"role": "AGE"}, "dob": {"role": "DATE_OF_BIRTH"}})


def test_total_candidates_and_mismatch():
    cols = ["total_income", "wage_income", "other_income", "age"]
    assert find_total_candidates(cols) == {"total_income": ["wage_income", "other_income"]}
    rows = [{"total_income": str(a + b), "wage_income": str(a), "other_income": str(b)} for a, b in zip(range(10, 30), range(5, 25), strict=True)]
    rows.append({"total_income": "999", "wage_income": "10", "other_income": "10"})
    result = detect_total_mismatch(rows, cols)
    assert result["total_income"]["evidence"] == [(20, 999.0, 20.0)]
    assert result["total_income"]["agree"] == 20


def test_total_mismatch_requires_established_relationship():
    cols = ["total_income", "wage_income", "other_income"]
    # Most rows don't add up -> not a sum relationship at all, so nothing reported.
    rows = [{"total_income": "100", "wage_income": str(i), "other_income": "1"} for i in range(20)]
    assert detect_total_mismatch(rows, cols) == {}


def test_total_relationship_confirmed_with_no_mismatch_is_reported_empty():
    cols = ["total_income", "wage_income", "other_income"]
    rows = [{"total_income": str(a + 5), "wage_income": str(a), "other_income": "5"} for a in range(20)]
    assert detect_total_mismatch(rows, cols) == {
        "total_income": {"components": ["wage_income", "other_income"], "evaluable": 20, "agree": 20, "evidence": []}
    }


def test_age_dob_prefers_visit_style_reference_date():
    roles = {
        "age": {"role": "AGE"},
        "dob": {"role": "DATE_OF_BIRTH"},
        "updated_date": {"role": "DATE"},
        "visit_date": {"role": "DATE"},
    }
    rows = [{"age": "38", "dob": "1985-03-04", "updated_date": "2026-01-01", "visit_date": "2024-01-10"}]
    # Against updated_date the age would look 2 years off; against the visit it's right.
    assert detect_age_dob_mismatch(rows, roles) == {}
