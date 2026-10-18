import pytest

from dataforensics.insights import (
    cramers_v,
    eta_squared,
    find_associations,
    find_trends,
    numeric_summaries,
    spearman,
)


def test_spearman_known_values():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    # Monotone but non-linear is still a perfect rank correlation.
    assert spearman([1, 2, 3, 4, 5], [1, 4, 9, 16, 1000]) == pytest.approx(1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None


def test_eta_squared_known_value():
    # Group means 1 and 3, grand mean 2: between = 2*1 + 2*1 = 4;
    # total = (0-2)^2 + (2-2)^2 + (2-2)^2 + (4-2)^2 = 8 -> 0.5.
    assert eta_squared({"a": [0, 2], "b": [2, 4]}) == pytest.approx(0.5)
    assert eta_squared({"a": [5, 5], "b": [5, 5]}) is None


def test_cramers_v_known_values():
    perfect = [("x", "p")] * 10 + [("y", "q")] * 10
    assert cramers_v(perfect) == pytest.approx(1.0)
    independent = [("x", "p")] * 5 + [("x", "q")] * 5 + [("y", "p")] * 5 + [("y", "q")] * 5
    assert cramers_v(independent) == pytest.approx(0.0)


def test_numeric_summaries_shape_and_exclusion():
    rows = [{"inc": str(v)} for v in [10] * 30 + [1000]] + [{"inc": "9999"}] * 5
    (summary,) = numeric_summaries(rows, ["inc"], exclude={"inc": {"9999"}})
    assert summary["N"] == 31  # the 9999 codes are excluded
    assert summary["Median"] == 10
    assert summary["Max"] == 1000
    assert summary["Shape"] == "strongly right-skewed"


def test_find_associations_ranks_and_describes_group_difference():
    rows = []
    for i in range(40):
        sex = "F" if i % 2 else "M"
        height = 160 + (i % 5) if sex == "F" else 178 + (i % 5)
        rows.append({"sex": sex, "height": str(height), "weight": str(height - 100), "noise": str((i * 7) % 11)})
    result = find_associations(rows, ["height", "weight", "noise"], ["sex"])
    top = result[0]
    assert top["Columns"] == "height ↔ weight"
    assert top["Effect"] == pytest.approx(1.0)
    group = next(r for r in result if r["Columns"] == "sex → height")
    assert "median 180 for sex = M vs 162 for sex = F" in group["Insight"]
    assert not any("noise" in r["Columns"] for r in result)


def test_find_associations_skips_redundant_pairs():
    rows = [{"a": str(i), "b": str(i * 2)} for i in range(40)]
    assert find_associations(rows, ["a", "b"], [], skip_pairs={frozenset(("a", "b"))}) == []


def test_find_trends_counts_and_direction():
    rows = [{"d": f"{2020 + i // 10}-01-{1 + i % 10:02d}", "cost": str(100 + i)} for i in range(40)]
    result = find_trends(rows, ["d"], ["cost"])
    assert result["period"] == "year"
    assert result["counts"] == [("2020", 10), ("2021", 10), ("2022", 10), ("2023", 10)]
    assert result["trends"] == [{"column": "cost", "rho": 1.0, "direction": "increasing"}]


def test_find_trends_without_dates_is_empty():
    assert find_trends([{"d": "n/a"}] * 50, ["d"], []) == {}


def test_find_trends_uses_months_for_short_spans_across_new_year():
    rows = [{"d": f"2023-12-{10 + i:02d}"} for i in range(15)] + [{"d": f"2024-01-{10 + i:02d}"} for i in range(15)]
    result = find_trends(rows, ["d"], [])
    assert result["period"] == "month"
    assert result["counts"] == [("2023-12", 15), ("2024-01", 15)]
