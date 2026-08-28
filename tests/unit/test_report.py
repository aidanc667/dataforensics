from dataforensics.report import render_html, render_markdown


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


def test_render_html_renders_uniform_dict_list_as_table():
    data = {"Findings": [{"Issue": "Duplicates", "Count": 14, "Severity": "High"}, {"Issue": "Outliers", "Count": 134, "Severity": "Review"}]}
    html = render_html("Audit", data)
    assert "<table>" in html
    assert "<th>Issue</th>" in html
    assert "<td>Duplicates</td>" in html
    assert "<td>134</td>" in html


def test_render_html_falls_back_to_bullets_for_non_uniform_dict_list():
    data = {"Mixed": [{"a": 1}, {"b": 2}]}
    html = render_html("Report", data)
    assert "<table>" not in html
    assert "<li>" in html


def test_render_markdown_renders_uniform_dict_list_as_table():
    data = {"Findings": [{"Issue": "Duplicates", "Count": 14, "Severity": "High"}]}
    md = render_markdown("Audit", data)
    assert "| Issue | Count | Severity |" in md
    assert "| --- | --- | --- |" in md
    assert "| Duplicates | 14 | High |" in md
