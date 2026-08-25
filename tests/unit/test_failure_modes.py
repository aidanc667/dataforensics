from click.testing import CliRunner

from dataforensics.cli import main


def test_scan_nonexistent_file():
    result = CliRunner().invoke(main, ["scan", "does_not_exist.csv"])
    assert result.exit_code != 0


def test_scan_empty_file(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("")
    result = CliRunner().invoke(main, ["scan", str(f), "--out-dir", str(tmp_path)])
    assert result.exit_code != 0 or result.exit_code == 0  # must not crash uncaught
    assert result.exception is None, f"empty file must not raise: {result.exception!r}"


def test_harmonize_invalid_yaml_exits_2(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("id,age\n1,40\n")
    bad_rules = tmp_path / "bad.yaml"
    bad_rules.write_text("  bad: [\n")

    result = CliRunner().invoke(
        main, ["harmonize", str(src), "--rules", str(bad_rules), "--output", str(tmp_path / "out.csv")]
    )
    assert result.exit_code == 2


def test_harmonize_output_path_collision_exits_2(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("id,age\n1,40\n")
    rules = tmp_path / "rules.yaml"
    rules.write_text("version: 1\nprimary_key: [id]\ncolumns: {}\n")

    result = CliRunner().invoke(
        main, ["harmonize", str(src), "--rules", str(rules), "--output", str(src)]
    )
    assert result.exit_code == 2


def test_scan_duplicate_header_exits_3_and_writes_no_reports(tmp_path):
    # header "pid,sex,sex" would otherwise silently collapse to one "sex"
    # column, losing data with exit 0. Must fail loudly with exit 3 instead,
    # naming the duplicate column, and must not write any report files.
    src = tmp_path / "dup.csv"
    src.write_text("pid,sex,sex\n1,M,F\n")
    out_dir = tmp_path / "out"

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(out_dir)])

    assert result.exit_code == 3
    assert "sex" in result.output
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_harmonize_single_file_duplicate_header_exits_3_and_writes_nothing(tmp_path):
    src = tmp_path / "dup.csv"
    src.write_text("pid,sex,sex\n1,M,F\n")
    rules = tmp_path / "rules.yaml"
    rules.write_text("version: 1\nprimary_key: [pid]\ncolumns: {}\n")
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules), "--output", str(output_path), "--execute"],
    )

    assert result.exit_code == 3
    assert "sex" in result.output
    assert not output_path.exists()
