from dataforensics.ingest import join_delimited_line, split_delimited_line, strip_footer


def test_strips_wonder_style_footer():
    lines = [
        "County,Deaths,Population",
        "Alameda,120,1600000",
        "Marin,45,260000",
        '"Query Parameters:"',
        '"Group By: County"',
        '"Total Deaths: 165"',
    ]
    data, stripped = strip_footer(lines, ",")
    assert data == lines[:3]
    assert stripped == lines[3:]


def test_no_footer_present_strips_nothing():
    lines = ["a,b", "1,2", "3,4", "5,6"]
    data, stripped = strip_footer(lines, ",")
    assert data == lines
    assert stripped == []


def test_single_stray_mismatched_line_not_stripped():
    # a lone short line (e.g. a genuinely short data row) should not trigger stripping
    lines = ["a,b,c", "1,2,3", "4,5", "7,8,9"]
    data, stripped = strip_footer(lines, ",")
    assert data == lines
    assert stripped == []


def test_quoted_delimiter_in_data_does_not_trigger_footer_misclassification():
    # Regression test for a real production bug: a standard, valid CSV with
    # quoted commas in the header AND data rows lost nearly all its rows,
    # because raw comma-counting (not quote-aware) saw a "field count
    # mismatch" on almost every data row. split_delimited_line (backed by
    # Python's csv module) must count a quoted comma as part of one field,
    # not as a field separator, so none of these rows get misclassified.
    lines = [
        'name,"role, title",age',
        'Bob,"Manager, East",34',
        '"Delta Clinic, North","Director, Ops",40',
        '"Acme Labs, South",Analyst,50',
    ]
    data, stripped = strip_footer(lines, ",")
    assert data == lines
    assert stripped == []


def test_split_delimited_line_respects_quoted_delimiter():
    assert split_delimited_line('"Delta Clinic, North",40', ",") == ["Delta Clinic, North", "40"]


def test_split_delimited_line_plain_fields():
    assert split_delimited_line("a,b,c", ",") == ["a", "b", "c"]


def test_split_delimited_line_empty_line_returns_single_empty_field():
    assert split_delimited_line("", ",") == [""]


def test_split_delimited_line_tab_delimiter():
    assert split_delimited_line("a\tb\tc", "\t") == ["a", "b", "c"]


def test_join_delimited_line_requotes_field_containing_delimiter():
    line = join_delimited_line(["Delta Clinic, North", "40"], ",")
    assert split_delimited_line(line, ",") == ["Delta Clinic, North", "40"]


def test_join_delimited_line_plain_fields_round_trip():
    line = join_delimited_line(["a", "b", "c"], ",")
    assert split_delimited_line(line, ",") == ["a", "b", "c"]
