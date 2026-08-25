import json

from click.testing import CliRunner

from dataforensics.cli import main


def _write_rules(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns:\n"
        "  age:\n"
        "    type: integer\n"
        "    minimum: 0\n"
        "    maximum: 120\n"
    )
    return rules_path


def test_scan_with_rules_flags_minimum_violation_as_error(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,age\n1,-5\n2,40\n")
    rules_path = _write_rules(tmp_path)

    result = CliRunner().invoke(
        main, ["scan", str(src), "--rules", str(rules_path), "--out-dir", str(tmp_path)]
    )

    assert result.exit_code == 1
    report = json.loads((tmp_path / "sample.validation_report.json").read_text())
    assert len(report["errors"]) == 1


def test_scan_does_not_flag_plausible_extreme_age_as_error(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,age\n1,95\n2,40\n")
    rules_path = _write_rules(tmp_path)

    result = CliRunner().invoke(
        main, ["scan", str(src), "--rules", str(rules_path), "--out-dir", str(tmp_path)]
    )

    assert result.exit_code == 0
    report = json.loads((tmp_path / "sample.validation_report.json").read_text())
    assert report["errors"] == []
    assert report["warnings"] == []


def test_scan_with_malformed_rules_exits_2(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,age\n1,40\n")
    bad_rules = tmp_path / "bad.yaml"
    bad_rules.write_text("not_a_valid_key: true\n")

    result = CliRunner().invoke(
        main, ["scan", str(src), "--rules", str(bad_rules), "--out-dir", str(tmp_path)]
    )
    assert result.exit_code == 2


def test_scan_with_unreadable_rules_file_exits_2(tmp_path):
    # A rules "file" that exists but can't be read as text (it's a directory)
    # exercises the OSError path in load_rules, not the YAMLError path.
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,age\n1,40\n")
    bad_rules_dir = tmp_path / "rules_is_a_dir.yaml"
    bad_rules_dir.mkdir()

    result = CliRunner().invoke(
        main, ["scan", str(src), "--rules", str(bad_rules_dir), "--out-dir", str(tmp_path)]
    )
    assert result.exit_code == 2
