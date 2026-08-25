import json

from click.testing import CliRunner

from datadiligence.cli import main


def test_report_renders_validation_report_to_stdout(tmp_path):
    artifact = tmp_path / "sample.validation_report.json"
    artifact.write_text(json.dumps({"errors": [], "warnings": [], "suggestions": [], "checks_evaluated": 1, "checks_passed": 1}))

    result = CliRunner().invoke(main, ["report", str(artifact)])
    assert result.exit_code == 0
    assert "# Validation Report" in result.output


def test_report_writes_to_out_path_when_given(tmp_path):
    artifact = tmp_path / "sample.data_dictionary.json"
    artifact.write_text(json.dumps({"age": {"dtype": "Utf8", "non_null_pct": 100.0}}))
    out_path = tmp_path / "rendered.md"

    result = CliRunner().invoke(main, ["report", str(artifact), "--out", str(out_path)])
    assert result.exit_code == 0
    assert out_path.exists()
    assert "# Data Dictionary" in out_path.read_text()


def test_report_renders_manifest_with_transformation_manifest_title(tmp_path):
    artifact = tmp_path / "sample.manifest.json"
    artifact.write_text(json.dumps({"run_id": "abc123", "mutations": []}))

    result = CliRunner().invoke(main, ["report", str(artifact)])
    assert result.exit_code == 0
    assert "# Transformation Manifest" in result.output


def test_report_on_malformed_json_exits_cleanly(tmp_path):
    artifact = tmp_path / "broken.json"
    artifact.write_text("{not valid json")

    result = CliRunner().invoke(main, ["report", str(artifact)])
    assert result.exit_code == 3
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "invalid" in result.output.lower() or "malformed" in result.output.lower()


def test_report_on_syntactically_valid_bare_list_exits_3_not_crash(tmp_path):
    # Syntactically-valid JSON whose top level isn't an object -- a bare
    # list -- used to reach viewer.classify_report and crash uncaught with
    # `AttributeError: 'list' object has no attribute 'keys'` instead of
    # failing cleanly like the malformed-JSON case above.
    artifact = tmp_path / "arr.json"
    artifact.write_text(json.dumps([1, 2, 3]))

    result = CliRunner().invoke(main, ["report", str(artifact)])
    assert result.exit_code == 3
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "invalid" in result.output.lower() or "malformed" in result.output.lower()


def test_report_on_syntactically_valid_null_exits_3_not_crash(tmp_path):
    # Same shape bug, different top-level value -- valid JSON `null` used to
    # crash with `TypeError: argument of type 'NoneType' is not iterable`.
    artifact = tmp_path / "null.json"
    artifact.write_text("null")

    result = CliRunner().invoke(main, ["report", str(artifact)])
    assert result.exit_code == 3
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "invalid" in result.output.lower() or "malformed" in result.output.lower()
