from rdh.report import render_html, render_markdown


def test_render_markdown_includes_column_names_and_values():
    data = {"age": {"dtype": "Utf8", "non_null_pct": 100.0, "unique_count": 3}}
    md = render_markdown("Data Dictionary", data)
    assert "# Data Dictionary" in md
    assert "age" in md
    assert "100.0" in md


def test_render_html_includes_title_and_values():
    data = {"age": {"dtype": "Utf8", "non_null_pct": 100.0, "unique_count": 3}}
    html = render_html("Data Dictionary", data)
    assert "<title>Data Dictionary</title>" in html
    assert "age" in html
    assert "100.0" in html
    assert "<!DOCTYPE html>" in html


def test_render_html_escapes_unsafe_characters():
    data = {"notes": {"sample": "<script>alert('x')</script>"}}
    html = render_html("Report", data)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
