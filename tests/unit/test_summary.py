import csv
import io

import pytest

from dataforensics.summary import (
    CHECKS,
    build_audit_trail,
    build_before_after,
    build_check_catalog,
    build_insights,
    build_null_table,
    rows_to_csv,
)


def _all_zero() -> dict:
    return {check[1]: 0 for check in CHECKS}


def test_catalog_has_more_than_twenty_checks_with_unique_ids():
    ids = [c[0] for c in CHECKS]
    keys = [c[1] for c in CHECKS]
    assert len(CHECKS) > 20
    assert len(set(ids)) == len(ids)
    assert len(set(keys)) == len(keys)


def test_catalog_status_reflects_count():
    results = _all_zero()
    results["missing_value_codes"] = 12
    results["clinical_ranges"] = None
    catalog = {row["Check ID"]: row for row in build_check_catalog(results)}

    assert catalog["DF-04"]["Status"] == "Flagged"
    assert catalog["DF-04"]["Items flagged"] == 12
    assert catalog["DF-01"]["Status"] == "Passed"
    assert catalog["DF-01"]["Items flagged"] == 0
    assert catalog["DF-04"]["Unit"] == "cells"
    assert catalog["DF-27"]["Status"] == "Not applicable"
    # None, not "", so the column stays numeric (sortable) in the UI table.
    assert catalog["DF-27"]["Items flagged"] is None


def test_catalog_rejects_missing_key_instead_of_reporting_passed():
    results = _all_zero()
    del results["whitespace"]
    with pytest.raises(ValueError, match="whitespace"):
        build_check_catalog(results)


def test_catalog_rejects_unknown_key():
    results = _all_zero()
    results["not_a_check"] = 1
    with pytest.raises(ValueError, match="not_a_check"):
        build_check_catalog(results)


def test_catalog_rejects_negative_count():
    results = _all_zero()
    results["duplicate_rows"] = -1
    with pytest.raises(ValueError, match="negative"):
        build_check_catalog(results)


def test_null_table_sorted_and_excludes_complete_columns():
    dictionary = {
        "id": {"null_count": 0},
        "age": {"null_count": 5},
        "income": {"null_count": 50},
    }
    table = build_null_table(dictionary, row_count=200)
    assert [r["Column"] for r in table] == ["income", "age"]
    assert table[0]["Null %"] == 25.0  # 50 / 200
    assert table[1]["Null %"] == 2.5  # 5 / 200
    # Column IDs follow the dictionary's column order, not the sort order.
    assert table[0]["Column ID"] == "C-03"
    assert table[1]["Column ID"] == "C-02"


def test_null_table_zero_rows_does_not_divide_by_zero():
    assert build_null_table({"a": {"null_count": 0}}, row_count=0) == []


def test_insights_cover_sentinels_and_missingness_by_age():
    insights = build_insights(
        sentinels={"income": ["9999"]},
        sentinel_counts={("income", "9999"): 37},
        missingness_concentration={
            "income": [
                {"column": "age", "direction": "higher", "median_when_missing": 71.0, "median_when_present": 44.0}
            ]
        },
        missingness_co_occurrence=[],
        top_code_values={},
        unit_inconsistency_columns=[],
        category_cluster_counts={},
    )
    assert [i["Type"] for i in insights] == ["Missing-value code", "Missingness pattern"]
    assert "37" in insights[0]["Insight"] and "'9999'" in insights[0]["Insight"]
    assert insights[1]["Column(s)"] == "income / age"
    assert "where age is higher" in insights[1]["Insight"]


def test_insights_cover_co_occurrence_top_coding_units_and_categories():
    insights = build_insights(
        sentinels={},
        sentinel_counts={},
        missingness_concentration={},
        missingness_co_occurrence=[
            {"column_a": "wages", "column_b": "hours", "both_missing_count": 1200, "overlap_fraction": 0.875}
        ],
        top_code_values={"age": "80"},
        unit_inconsistency_columns=["weight"],
        category_cluster_counts={"sex": 2},
    )
    by_type = {i["Type"]: i for i in insights}
    assert by_type["Missingness pattern"]["Column(s)"] == "wages / hours"
    assert "1,200 row(s)" in by_type["Missingness pattern"]["Insight"]
    assert "88%" in by_type["Missingness pattern"]["Insight"]  # 0.875 rounds to 88%
    assert "pile up at 80" in by_type["Top-coding"]["Insight"]
    assert by_type["Mixed units"]["Column(s)"] == "weight"
    assert by_type["Inconsistent categories"]["Insight"].startswith("2 group(s)")


def test_insights_empty_when_nothing_found():
    assert (
        build_insights(
            sentinels={},
            sentinel_counts={},
            missingness_concentration={},
            missingness_co_occurrence=[],
            top_code_values={},
            unit_inconsistency_columns=[],
            category_cluster_counts={},
        )
        == []
    )


def test_audit_trail_preserves_order_and_fields():
    mutations = [
        {
            "row_key": {"participant_id": "P001"},
            "column": "income",
            "original_value": "9999",
            "new_value": "",
            "transformation_rule": "missing_value_sentinel:income",
            "reason": "Approved",
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
        },
        {
            "row_key": {"site": "A", "visit": "2"},
            "column": "sex",
            "original_value": "f",
            "new_value": "F",
            "transformation_rule": "category_mapping:sex",
            "reason": "Approved",
        },
    ]
    trail = build_audit_trail(mutations)
    assert [r["Change #"] for r in trail] == [1, 2]
    assert trail[0]["Row key"] == "participant_id=P001"
    assert trail[1]["Row key"] == "site=A, visit=2"
    assert trail[0]["Original value"] == "9999"
    assert trail[0]["New value"] == ""
    assert trail[1]["Applied (UTC)"] == ""


def test_audit_trail_masks_pii_like_key_columns():
    trail = build_audit_trail(
        [
            {
                "row_key": {"email": "a@b.com", "visit": None},
                "column": "sex",
                "original_value": "f",
                "new_value": "F",
                "transformation_rule": "category_mapping:sex",
                "reason": "Approved",
            }
        ]
    )
    assert "a@b.com" not in trail[0]["Row key"]
    assert trail[0]["Row key"] == "email=[masked], visit="


def test_rows_to_csv_round_trips_and_handles_empty():
    rows = [{"a": "x, y", "b": 1}]
    parsed = list(csv.DictReader(io.StringIO(rows_to_csv(rows))))
    assert parsed == [{"a": "x, y", "b": "1"}]
    assert rows_to_csv([]) == ""


def test_before_after_reports_scores_and_only_changed_checks():
    q_before = {"overall": 70, "completeness": 90, "consistency": 60, "validity": 50, "uniqueness": 100, "structural_quality": 100}
    q_after = {**q_before, "overall": 85, "validity": 95, "completeness": 85}
    before = _all_zero()
    before.update(missing_value_codes=116, whitespace=4, statistical_outliers=13)
    after = _all_zero()
    after.update(statistical_outliers=13)
    rows = build_before_after(q_before, q_after, build_check_catalog(before), build_check_catalog(after))
    by_measure = {r["Measure"]: r for r in rows}
    assert by_measure["Quality score (overall)"] == {"Measure": "Quality score (overall)", "Before": 70, "After": 85, "Change": 15}
    assert by_measure["Completeness"]["Change"] == -5
    changed_checks = [r["Measure"] for r in rows[6:]]
    assert changed_checks == [
        "DF-04 Missing-value codes stored as real values (e.g. 9999, -99) (cells)",
        "DF-21 Leading/trailing or doubled whitespace (cells)",
    ]
    assert rows[6]["Change"] == -116


def test_before_after_rejects_mismatched_catalogs():
    q = {k: 100 for k in ("overall", "completeness", "consistency", "validity", "uniqueness", "structural_quality")}
    with pytest.raises(ValueError, match="DF-01"):
        build_before_after(q, q, build_check_catalog(_all_zero()), [])


def test_every_check_has_a_description_and_handling():
    from dataforensics.summary import CHECK_DETAILS, build_check_reference

    assert set(CHECK_DETAILS) == {c[1] for c in CHECKS}
    reference = build_check_reference()
    assert len(reference) == len(CHECKS)
    assert all(r["What happens"].startswith(("Fix on approval", "Flag only")) for r in reference)
