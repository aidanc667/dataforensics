import datetime

from dataforensics.ingest import IngestFormatError, _stringify_cell, detect_file_format


def test_detect_file_format_csv():
    assert detect_file_format_from_name("sample.csv") == "delimited"


def test_detect_file_format_tsv():
    assert detect_file_format_from_name("sample.tsv") == "delimited"


def test_detect_file_format_unrecognized_extension_is_delimited():
    assert detect_file_format_from_name("sample.txt") == "delimited"


def test_detect_file_format_json():
    assert detect_file_format_from_name("sample.json") == "json"


def test_detect_file_format_xlsx():
    assert detect_file_format_from_name("sample.xlsx") == "excel"


def test_detect_file_format_xls():
    assert detect_file_format_from_name("sample.xls") == "excel"


def test_detect_file_format_case_insensitive():
    assert detect_file_format_from_name("SAMPLE.JSON") == "json"
    assert detect_file_format_from_name("SAMPLE.XLSX") == "excel"


def detect_file_format_from_name(name: str) -> str:
    from pathlib import Path

    return detect_file_format(Path(name))


def test_ingest_format_error_is_an_exception():
    assert issubclass(IngestFormatError, Exception)


def test_stringify_cell_none_is_empty_string():
    assert _stringify_cell(None) == ""


def test_stringify_cell_bool_is_lowercase():
    assert _stringify_cell(True) == "true"
    assert _stringify_cell(False) == "false"


def test_stringify_cell_int():
    assert _stringify_cell(5) == "5"


def test_stringify_cell_float_non_whole():
    assert _stringify_cell(5.5) == "5.5"


def test_stringify_cell_float_whole_number_has_no_trailing_point_zero():
    # A spreadsheet cell holding "34" should stringify as "34", not "34.0" --
    # both openpyxl and xlrd can hand back a whole number as a float
    # depending on cell formatting, and the two backends must agree.
    assert _stringify_cell(34.0) == "34"


def test_stringify_cell_date():
    assert _stringify_cell(datetime.date(2024, 1, 15)) == "2024-01-15"


def test_stringify_cell_datetime():
    assert _stringify_cell(datetime.datetime(2024, 1, 15, 0, 0, 0)) == "2024-01-15T00:00:00"


def test_stringify_cell_string_passthrough():
    assert _stringify_cell("hello") == "hello"


def test_stringify_cell_bool_checked_before_int():
    # bool is a subclass of int in Python -- True must not stringify as "1".
    assert _stringify_cell(True) != "1"
