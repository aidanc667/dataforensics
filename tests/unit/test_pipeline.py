import csv
from pathlib import Path

import pytest

from dataforensics.pipeline import run_investigation

DEMOS = Path(__file__).parent.parent.parent / "fixtures" / "demos"


def _run(name: str, dataset_type: str = "General") -> dict:
    path = DEMOS / name
    return run_investigation(path, sheet=None, dataset_type=dataset_type, data_size=path.stat().st_size)


def test_brfss_flags_9999_codes_and_weight_heaping():
    result = _run("brfss_survey_sample.csv")
    assert set(result["sentinels"]["weight_lbs"]) == {"7777", "9999"}
    catalog = {c["Check ID"]: c for c in result["check_catalog"]}
    assert catalog["DF-04"]["Status"] == "Flagged"
    assert "weight_lbs" in result["heaping_findings"]
    # 9999/7777 codes are excluded from numeric reads: the summary counts
    # every numeric weight except those codes. (BRFSS also stores some
    # weights as 9000 + kg, e.g. 9088 -- real values, so they stay in.)
    with open(DEMOS / "brfss_survey_sample.csv") as fh:
        raw = [r["weight_lbs"].strip() for r in csv.DictReader(fh)]
    expected_n = sum(1 for v in raw if v.isdigit() and v not in {"7777", "9999"})
    weight = next(s for s in result["numeric_summary"] if s["Column"] == "weight_lbs")
    assert weight["N"] == expected_n
    assert weight["Max"] not in (7777, 9999)


def test_acs_reports_missingness_tied_to_age():
    result = _run("acs_pums_person_dc.csv")
    pairs = {(g["column"], g["group_column"]) for g in result["group_missingness"]}
    assert ("wages_income", "age_years") in pairs


def test_profile_checks_not_applicable_under_general():
    catalog = {c["Check ID"]: c for c in _run("messy_csv_example.csv")["check_catalog"]}
    assert catalog["DF-27"]["Status"] == "Not applicable"
    assert len(catalog) > 30


def test_unknown_dataset_type_rejected():
    with pytest.raises(ValueError, match="dataset_type"):
        _run("messy_csv_example.csv", dataset_type="Nope")


def test_first_name_plus_sex_alone_is_not_entity_evidence(tmp_path):
    # Regression: common first names + sex collided constantly, flagging
    # clean data as "same entity under two IDs".
    lines = ["participant_id,first_name,sex"] + [f"P{i},{['Ann', 'Bob'][i % 2]},{'FM'[i % 2]}" for i in range(40)]
    path = tmp_path / "people.csv"
    path.write_text("\n".join(lines) + "\n")
    result = run_investigation(path, sheet=None, dataset_type="General", data_size=1)
    catalog = {c["Check ID"]: c for c in result["check_catalog"]}
    assert result["duplicate_entities"] == []
    assert catalog["DF-02"]["Status"] == "Not applicable"
    assert catalog["DF-03"]["Status"] == "Not applicable"


def test_rare_combination_rows_are_real_row_numbers_on_large_files(tmp_path):
    # Regression (review): on files over the 20,000-row sample, evidence
    # gave positions within the sample instead of real row numbers.
    lines = ["id,sex,pregnant"]
    for i in range(30_000):
        sex = "F" if i % 2 else "M"
        pregnant = "Yes" if sex == "F" and i % 4 == 1 else "No"
        lines.append(f"{i},{sex},{pregnant}")
    lines[25_001] = "25000,M,Yes"  # the one impossible combination, data row 25,001
    path = tmp_path / "big.csv"
    path.write_text("\n".join(lines) + "\n")
    result = run_investigation(path, sheet=None, dataset_type="General", data_size=1)
    # Row 25,000 is in the sample: int(16,667 x 1.5) = 25,000.
    (combo,) = [f for f in result["rare_combination_findings"] if f["values"] == ("M", "Yes")]
    assert combo["rows"] == [25_000]
    assert result["rows"][25_000]["sex"] == "M" and result["rows"][25_000]["pregnant"] == "Yes"


def test_auto_profile_picks_checks_from_the_columns():
    result = _run("messy_csv_example.csv", dataset_type="Auto")
    # participant_id -> the clinical same-ID check; no age/FIPS/ZIP/weight columns.
    assert result["profile_checks"] == ["conflicting_id_records"]
    assert result["detected_profiles"] == ["Clinical / Research"]
    brfss = _run("brfss_survey_sample.csv", dataset_type="Auto")
    assert "fips_format" in brfss["profile_checks"] and "clinical_ranges" in brfss["profile_checks"]


def test_score_counts_the_format_problems_cleaning_fixes(tmp_path):
    """Regression (user report: "the score doesn't really move"): lost
    leading zeros, numbers written as text and whitespace-only cells were
    fixed by cleaning but never counted, so fixing them changed nothing."""
    path = tmp_path / "planted.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rid", "zip", "amount", "note", "city"])
        for i in range(20):
            writer.writerow([
                i + 1,
                "2134" if i < 2 else 20001 + i,  # 2 ZIPs that lost a leading zero
                "$1,200" if i < 3 else 100 + i,  # 3 numbers written as text
                "   " if i < 4 else f"text{i}",  # 4 whitespace-only cells
                "Boston",
            ])
    result = run_investigation(path, sheet=None, dataset_type="Auto", data_size=1)
    # 2 + 3 + 4 (a whitespace-only cell is padding, and also missing)
    assert result["format_integrity_flagged_cell_count"] == 9
    # 4 of 100 cells missing -> 0.96 ** 2 = 0.9216 -> 92
    assert result["quality"]["completeness"] == 92


def test_default_key_is_a_unique_one():
    # Regression (accuracy audit): household_id alone repeats in ACS, so the
    # audit trail couldn't say which person a change was made to.
    from dataforensics.pipeline import suggest_primary_key

    acs = [{"household_id": f"H{i // 3}", "person_number": str(i % 3 + 1), "age": str(20 + i)} for i in range(9)]
    assert suggest_primary_key(acs, ["household_id", "person_number", "age"], ["household_id"]) == [
        "household_id", "person_number"
    ]
    # A unique single ID column wins over combining a repeating one.
    visits = [{"subject_id": f"S{i // 2}", "record_id": f"R{i}", "visit": str(i % 2)} for i in range(6)]
    assert suggest_primary_key(visits, ["subject_id", "record_id", "visit"], ["subject_id", "record_id"]) == ["record_id"]
    unique = [{"id": str(i)} for i in range(4)]
    assert suggest_primary_key(unique, ["id"], ["id"]) == ["id"]


def test_the_missing_label_still_counts_as_missing(tmp_path):
    # Regression (accuracy audit): text codes are mapped to "Missing", which
    # counted as filled -- completeness rose although nothing was recovered.
    def completeness(fill: str) -> int:
        path = tmp_path / f"f{len(fill)}.csv"
        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["id", "status"])
            for i in range(20):
                writer.writerow([i, fill if i < 4 else ("Never" if i % 2 else "Current")])
        return run_investigation(path, sheet=None, dataset_type="Auto", data_size=1)["quality"]["completeness"]

    # 4 of 40 cells have no answer either way: 0.9 ** 2 = 0.81 -> 81.
    assert completeness("") == completeness("Missing") == 81


def test_default_key_skips_place_codes_personal_data_and_blank_ids():
    from dataforensics.pipeline import suggest_primary_key

    # A unique ZIP still identifies a place, not a record: the row ID wins.
    rows = [{"rid": f"R{i}", "zip": f"0{1000 + i}"} for i in range(5)]
    assert suggest_primary_key(rows, ["rid", "zip"], ["zip"]) == ["rid"]
    # A repeating household ID is never paired with a date of birth.
    people = [{"household_id": f"H{i // 2}", "birth_date": f"19{50 + i}-01-01", "x": str(i)} for i in range(6)]
    assert suggest_primary_key(people, ["household_id", "birth_date", "x"], ["household_id"]) == ["household_id"]
    # An ID with a blank cell isn't unique; with no partner the first ID stays the default.
    blank = [{"id": "" if i == 2 else str(i)} for i in range(4)]
    assert suggest_primary_key(blank, ["id"], ["id"]) == ["id"]


def test_codebook_adds_no_answer_codes_and_converts_coded_units():
    """BRFSS with its codebook: 7/9 (Don't know/Refused) become missing-value
    codes, and weights recorded as 9000 + kg are read in pounds. Expected
    statistics checked with pandas: mean 183.08 lb, max 425 (without the
    codebook: mean 236.06 with a 9,088 maximum)."""
    from dataforensics.codebook import parse_codebook

    codebook = parse_codebook((DEMOS / "brfss_survey_sample.codebook.csv").read_bytes())
    result = run_investigation(DEMOS / "brfss_survey_sample.csv", sheet=None, dataset_type="Auto", data_size=1,
                               codebook=codebook)
    assert result["sentinels"]["general_health"] == ["7", "9"]
    assert result["sentinels"]["income_bracket"] == ["77", "99"]  # 7 and 9 are real brackets here
    assert result["unit_conversions"]["weight_lbs"] == {"9055": "121", "9060": "132", "9088": "194"}
    weight = next(s for s in result["numeric_summary"] if s["Column"] == "weight_lbs")
    assert (weight["Mean"], weight["Max"]) == (183.08, 425.0)


def test_codebook_codes_are_not_outliers_in_a_numeric_column(tmp_path):
    """Review finding: a days column (0-30) with 77 = Don't know and 99 =
    Refused had 77/99 counted as IQR outliers even with the codebook."""
    path = tmp_path / "days.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "days_drank"])
        for i in range(200):
            writer.writerow([i, "99" if i % 20 == 0 else "77" if i % 25 == 0 else str(i % 31)])
    from dataforensics.codebook import parse_codebook

    codebook = parse_codebook(b"column,value,label\ndays_drank,77,Don't know\ndays_drank,99,Refused\n")
    without = run_investigation(path, sheet=None, dataset_type="Auto", data_size=1)
    with_codebook = run_investigation(path, sheet=None, dataset_type="Auto", data_size=1, codebook=codebook)
    assert without["dictionary"]["days_drank"]["outliers"]["outlier_count"] > 0  # 77s and 99s
    assert with_codebook["dictionary"]["days_drank"]["outliers"]["outlier_count"] == 0  # values 0-30 only
    assert with_codebook["sentinels"]["days_drank"] == ["77", "99"]
