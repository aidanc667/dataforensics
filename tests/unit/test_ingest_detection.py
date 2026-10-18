from dataforensics.ingest import (
    detect_delimiter,
    detect_encoding,
    read_source_lines,
    split_delimited_line,
)


def test_read_source_lines_strips_leading_utf8_bom(tmp_path):
    # Excel's "CSV UTF-8" export always prepends a BOM. Left in place, it
    # decodes as a literal U+FEFF character glued to the first header name
    # (e.g. "﻿id"), silently breaking every rules-file column match
    # against that column.
    f = tmp_path / "bom.csv"
    f.write_bytes(b"\xef\xbb\xbfid,name\n1,Alice\n")
    lines, _ = read_source_lines(f)
    assert lines[0] == "id,name"


def test_split_delimited_line_handles_field_over_stdlib_csv_default_limit():
    # The stdlib csv module's default 128KB per-field cap previously crashed
    # any scan of a file with a longer free-text cell (a clinical note, an
    # "other, please specify" survey response) with an unhandled _csv.Error,
    # since split_delimited_line parses each line through csv.reader.
    long_value = "x" * 200_000
    line = f"1,{long_value}"
    assert split_delimited_line(line, ",") == ["1", long_value]


def test_detect_delimiter_comma():
    lines = ["a,b,c", "1,2,3", "4,5,6"]
    assert detect_delimiter(lines) == ","


def test_detect_delimiter_tab():
    lines = ["a\tb\tc", "1\t2\t3", "4\t5\t6"]
    assert detect_delimiter(lines) == "\t"


def test_detect_delimiter_semicolon():
    lines = ["a;b;c", "1;2;3", "4;5;6"]
    assert detect_delimiter(lines) == ";"


def test_detect_encoding_utf8(tmp_path):
    f = tmp_path / "utf8.csv"
    f.write_text("name,city\nJosé,São Paulo\n", encoding="utf-8")
    assert detect_encoding(f) == "utf-8" or detect_encoding(f).lower() == "utf-8"


def test_detect_encoding_latin1(tmp_path):
    f = tmp_path / "latin1.csv"
    f.write_bytes("name,city\nJos\xe9,Caf\xe9\n".encode("latin-1"))
    # This sample has only one accented byte, which is genuinely ambiguous
    # across several similar single-byte code pages (charset_normalizer's
    # honest best guess can legitimately land on any of these depending on
    # its installed version/heuristics). Accepting the full set of plausible
    # detections is correct here, not a bug to paper over: dataforensics reports what
    # was detected rather than silently coercing it to a single "canonical"
    # answer.
    assert detect_encoding(f).lower() in (
        "latin-1", "iso-8859-1", "cp1252", "cp1250",
    )


def test_deduplicate_header_appends_positional_suffix():
    from dataforensics.ingest import deduplicate_header

    assert deduplicate_header(["id", "name", "name", "name"]) == ["id", "name", "name_2", "name_3"]


def test_deduplicate_header_no_change_when_no_duplicates():
    from dataforensics.ingest import deduplicate_header

    assert deduplicate_header(["id", "age", "sex"]) == ["id", "age", "sex"]


def test_small_valid_utf8_file_is_not_misread_as_another_encoding(tmp_path):
    # Regression: charset_normalizer guessed Windows-1251 for this file,
    # turning "An\u200bn" into "AnвЂ‹n" on load.
    from dataforensics.dictionary import read_rows

    f = tmp_path / "x.csv"
    f.write_bytes("id,name,city\n1,An\u200bn,Den\u200bver\n2,Bob\xa0,X\n3,\u200b,Y\n".encode("utf-8"))
    assert detect_encoding(f) == "utf-8"
    rows = read_rows(f)
    assert rows[0]["name"] == "An\u200bn"
    assert rows[1]["name"] == "Bob\xa0"


def test_utf16_files_are_not_taken_for_utf8(tmp_path):
    # BOM-less UTF-16 of ASCII text is valid UTF-8 byte for byte (with NULs),
    # so the UTF-8 shortcut must not claim it.
    from dataforensics.dictionary import read_rows

    for name, data in (
        ("bom.csv", "id,name\n1,Zoë\n2,Ann\n".encode("utf-16")),
        ("nobom.csv", "id,name\n1,Zoe\n2,Ann\n".encode("utf-16-le")),
    ):
        f = tmp_path / name
        f.write_bytes(data)
        assert detect_encoding(f) != "utf-8", name
        rows = read_rows(f)
        assert [r["name"] for r in rows][-1] == "Ann", name
        assert "\x00" not in "".join(rows[0].keys()), name
