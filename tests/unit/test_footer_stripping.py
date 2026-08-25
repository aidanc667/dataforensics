from rdh.ingest import strip_footer


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
