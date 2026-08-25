from rdh.report import render_markdown


def test_render_markdown_includes_column_names_and_values():
    data = {"age": {"dtype": "Utf8", "non_null_pct": 100.0, "unique_count": 3}}
    md = render_markdown("Data Dictionary", data)
    assert "# Data Dictionary" in md
    assert "age" in md
    assert "100.0" in md
