from pathlib import Path

import pytest

from dataforensics.dictionary import build_data_dictionary, read_rows
from dataforensics.ingest import DuplicateHeaderError


def test_dictionary_basic_fields(tmp_path):
    f = tmp_path / "sample.csv"
    f.write_text(
        "participant_id,sex,age,notes\n"
        "001,M,34,fine\n"
        "002,F,29,fine\n"
        "003,M,,fine\n"
        "004,F,41,fine\n"
    )
    d = build_data_dictionary(f)

    assert d["participant_id"]["category"] == "id"
    assert d["participant_id"]["dtype"] == "Utf8"

    assert d["age"]["null_count"] == 1
    assert d["age"]["non_null_pct"] == 75.0

    assert d["sex"]["category"] == "categorical"
    assert set(d["sex"]["levels"]) == {"M", "F"}


def test_dictionary_zero_variance_flag(tmp_path):
    f = tmp_path / "flat.csv"
    f.write_text("id,site\n001,A\n002,A\n003,A\n")
    d = build_data_dictionary(f)
    assert d["site"]["is_zero_variance"] is True
    assert d["id"]["is_zero_variance"] is False


def test_dictionary_zero_vs_null_kept_separate(tmp_path):
    f = tmp_path / "smoking.csv"
    f.write_text("id,cigs_per_day\n001,0\n002,\n003,5\n")
    d = build_data_dictionary(f)
    assert d["cigs_per_day"]["zero_count"] == 1
    assert d["cigs_per_day"]["null_count"] == 1


def test_dictionary_high_cardinality_is_free_text(tmp_path):
    rows = "\n".join(f"{i},note-{i}-unique" for i in range(60))
    f = tmp_path / "notes.csv"
    f.write_text("id,note\n" + rows + "\n")
    d = build_data_dictionary(f)
    assert d["note"]["category"] == "free_text"


def test_dictionary_small_sample_categorical_not_suppressed_by_ratio_cap(tmp_path):
    # With only 4 data rows, a naive 5%-of-N cardinality cap rounds to 0/1,
    # which would wrongly push a normal 2-level column like "sex" into
    # free_text. A floor on the cap keeps small files usable.
    f = tmp_path / "sample.csv"
    f.write_text(
        "id,sex\n001,M\n002,F\n003,M\n004,F\n"
    )
    d = build_data_dictionary(f)
    assert d["sex"]["category"] == "categorical"
    assert set(d["sex"]["levels"]) == {"M", "F"}


def test_dictionary_ragged_row_counts_missing_trailing_field_as_null(tmp_path):
    # A data row with fewer fields than the header (a common malformed-export
    # pattern) must count the missing trailing field as null rather than
    # silently dropping it from that column's value list.
    f = tmp_path / "ragged.csv"
    f.write_text("a,b,c\n1,2,3\n4,5\n")
    d = build_data_dictionary(f)
    assert d["c"]["null_count"] == 1
    assert d["c"]["non_null_pct"] == 50.0


def test_read_rows_ragged_row_fills_missing_trailing_field(tmp_path):
    f = tmp_path / "ragged.csv"
    f.write_text("participant_id,age,site\n1,40,A\n2,41\n")  # row 2 is missing the 'site' field
    rows = read_rows(f)
    assert rows[1] == {"participant_id": "2", "age": "41", "site": ""}


def test_read_rows_empty_file_returns_empty_list(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("")
    assert read_rows(f) == []


def test_read_rows_duplicate_header_raises_instead_of_silently_dropping_data(tmp_path):
    # header "pid,sex,sex" with values "1,M,F": without duplicate-header
    # detection, dict(zip_longest(...)) collapses both "sex" columns into
    # one key and the "M" value is silently lost with no error, exit 0.
    f = tmp_path / "dup.csv"
    f.write_text("pid,sex,sex\n1,M,F\n")
    with pytest.raises(DuplicateHeaderError, match="sex"):
        read_rows(f)


def test_build_data_dictionary_duplicate_header_raises(tmp_path):
    f = tmp_path / "dup.csv"
    f.write_text("pid,sex,sex\n1,M,F\n")
    with pytest.raises(DuplicateHeaderError, match="sex"):
        build_data_dictionary(f)


import json


def test_build_data_dictionary_from_json(tmp_path):
    f = tmp_path / "sample.json"
    f.write_text(json.dumps([
        {"participant_id": "001", "sex": "M", "age": 34, "notes": "fine"},
        {"participant_id": "002", "sex": "F", "age": 29, "notes": "fine"},
        {"participant_id": "003", "sex": "M", "age": None, "notes": "fine"},
        {"participant_id": "004", "sex": "F", "age": 41, "notes": "fine"},
    ]))
    d = build_data_dictionary(f)

    assert d["participant_id"]["category"] == "id"
    assert d["age"]["null_count"] == 1
    assert d["age"]["non_null_pct"] == 75.0
    assert d["sex"]["category"] == "categorical"
    assert set(d["sex"]["levels"]) == {"M", "F"}


def test_read_rows_from_json(tmp_path):
    f = tmp_path / "sample.json"
    f.write_text(json.dumps([{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]))
    assert read_rows(f) == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_build_data_dictionary_from_excel(tmp_path):
    import openpyxl

    f = tmp_path / "sample.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["participant_id", "sex", "age"])
    ws.append(["001", "M", 34])
    ws.append(["002", "F", 29])
    ws.append(["003", "M", None])
    ws.append(["004", "F", 41])
    wb.save(f)

    d = build_data_dictionary(f)
    assert d["participant_id"]["category"] == "id"
    assert d["age"]["null_count"] == 1
    assert d["sex"]["category"] == "categorical"


def test_read_rows_from_excel_with_explicit_sheet(tmp_path):
    import openpyxl

    f = tmp_path / "multi.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "First"
    ws1.append(["a"])
    ws1.append(["1"])
    ws2 = wb.create_sheet("Second")
    ws2.append(["b"])
    ws2.append(["2"])
    wb.save(f)

    assert read_rows(f, sheet="Second") == [{"b": "2"}]


def test_build_data_dictionary_empty_json_array_returns_empty_dict(tmp_path):
    f = tmp_path / "empty.json"
    f.write_text("[]")
    assert build_data_dictionary(f) == {}
