import csv
import json

from click.testing import CliRunner

from rdh.cli import main
from rdh.hashing import sha256_file


def _write_input_and_rules(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,smoking_status\n1,99\n2,10\n3,99\n")
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  smoking_status:\n"
        "    \"99\": Refused\n"
    )
    return src, rules_path


def test_execute_applies_sentinel_rule_and_writes_manifest(tmp_path):
    src, rules_path = _write_input_and_rules(tmp_path)
    before_hash = sha256_file(src)
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        [
            "harmonize",
            str(src),
            "--rules",
            str(rules_path),
            "--output",
            str(output_path),
            "--execute",
        ],
    )

    assert result.exit_code == 0
    assert sha256_file(src) == before_hash  # input untouched
    assert output_path.exists()

    with open(output_path) as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["smoking_status"] == "Refused"
    assert rows[1]["smoking_status"] == "10"
    assert rows[2]["smoking_status"] == "Refused"

    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["mutations"]) == 2
    assert manifest["mutations"][0]["row_key"] == {"participant_id": "1"}
    assert manifest["mutations"][0]["original_value"] == "99"
    assert manifest["mutations"][0]["new_value"] == "Refused"
    assert manifest["mutations"][0]["transformation_rule"] == "missing_value_sentinel:smoking_status"


def test_execute_is_idempotent(tmp_path):
    src, rules_path = _write_input_and_rules(tmp_path)
    output_path = tmp_path / "out.csv"

    CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )
    first_bytes = output_path.read_bytes()

    output_path.unlink()
    (output_path.with_suffix(output_path.suffix + ".manifest.json")).unlink()

    CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"],
    )
    second_bytes = output_path.read_bytes()

    assert first_bytes == second_bytes


def test_execute_on_header_only_csv_preserves_columns(tmp_path):
    src = tmp_path / "empty.csv"
    src.write_text("participant_id,smoking_status\n")
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\nprimary_key: [participant_id]\ncolumns: {}\n"
        "missing_values:\n  smoking_status:\n    \"99\": Refused\n"
    )
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main, ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path), "--execute"]
    )
    assert result.exit_code == 0
    content = output_path.read_text()
    assert content.strip().startswith("participant_id,smoking_status") or content.strip() == "participant_id,smoking_status"
