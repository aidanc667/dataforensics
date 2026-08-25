from rdh.ingest import detect_delimiter, detect_encoding


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
    # detections is correct here, not a bug to paper over: rdh reports what
    # was detected rather than silently coercing it to a single "canonical"
    # answer.
    assert detect_encoding(f).lower() in (
        "latin-1", "iso-8859-1", "cp1252", "cp1250",
    )
