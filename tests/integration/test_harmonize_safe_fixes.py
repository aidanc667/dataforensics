"""The rules file the app's "Download rules" button writes must be
re-runnable with `dataforensics harmonize` and produce the same cleaning."""

import csv
import json

import yaml
from click.testing import CliRunner

from dataforensics.cli import main


def _setup(tmp_path, extra_rules: dict):
    src = tmp_path / "in.csv"
    src.write_text(
        "id,city,income,zip,age\n"
        "1, Denver ,\"$50,000\",8001,34\n"
        "2,Boulder,40000,80302,150\n"
        "1, Denver ,\"$50,000\",8001,34\n"
    )
    rules = {
        "version": 1,
        "primary_key": ["id"],
        "columns": {},
        "missing_values": {},
        "category_mappings": {},
        **extra_rules,
    }
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(yaml.safe_dump(rules))
    return src, rules_path


def _run(src, rules_path, out, *flags):
    return CliRunner().invoke(main, ["harmonize", str(src), "--rules", str(rules_path), "--output", str(out), *flags])


def test_execute_applies_safe_fixes_dedup_and_expectations(tmp_path):
    src, rules_path = _setup(
        tmp_path,
        {
            "safe_fixes": {
                "whitespace": ["city"],
                "numeric_format": ["income"],
                "leading_zeros": {"zip": 5},
                "drop_exact_duplicate_rows": True,
            },
            "expectations": [{"type": "range", "column": "age", "min": 0, "max": 120}],
        },
    )
    out = tmp_path / "out.csv"
    result = _run(src, rules_path, out, "--execute")
    assert result.exit_code == 0, result.output
    assert "expectation 'age is between 0 and 120' violated on 1 row(s)" in result.output

    with open(out) as fh:
        rows = list(csv.DictReader(fh))
    assert rows == [
        {"id": "1", "city": "Denver", "income": "50000", "zip": "08001", "age": "34"},
        {"id": "2", "city": "Boulder", "income": "40000", "zip": "80302", "age": "150"},
    ]
    manifest = json.loads((tmp_path / "out.csv.manifest.json").read_text())
    rules_applied = [m["transformation_rule"] for m in manifest["mutations"]]
    assert rules_applied.count("drop_exact_duplicate_row") == 1
    assert rules_applied.count("whitespace:city") == 2
    assert manifest["expectations"][0]["violations"] == 1


def test_dry_run_previews_safe_fixes_without_writing(tmp_path):
    src, rules_path = _setup(tmp_path, {"safe_fixes": {"whitespace": ["city"], "drop_exact_duplicate_rows": True}})
    out = tmp_path / "out.csv"
    result = _run(src, rules_path, out)
    assert result.exit_code == 0, result.output
    assert "whitespace:city: 2 cells affected" in result.output
    assert "drop_exact_duplicate_row: 1 rows would be removed" in result.output
    assert not out.exists()


def test_malformed_safe_fixes_rejected(tmp_path):
    src, rules_path = _setup(tmp_path, {"safe_fixes": {"leading_zeros": {"zip": "five"}}})
    result = _run(src, rules_path, tmp_path / "out.csv", "--execute")
    assert result.exit_code == 2
    assert "safe_fixes" in result.output


def test_rules_naming_absent_columns_fail_even_in_dry_run(tmp_path):
    src, rules_path = _setup(
        tmp_path,
        {"safe_fixes": {"whitespace": ["county"]}, "expectations": [{"type": "required", "column": "dob"}]},
    )
    result = _run(src, rules_path, tmp_path / "out.csv")
    assert result.exit_code == 2
    assert "county" in result.output and "dob" in result.output


def test_malformed_expectation_fails_before_writing(tmp_path):
    src, rules_path = _setup(tmp_path, {"expectations": [{"type": "range", "column": "age"}]})
    out = tmp_path / "out.csv"
    result = _run(src, rules_path, out, "--execute")
    assert result.exit_code == 2
    assert "needs a min" in result.output
    assert not out.exists()


def test_safe_fix_setting_of_wrong_shape_is_a_clean_error(tmp_path):
    src, rules_path = _setup(tmp_path, {"safe_fixes": {"leading_zeros": ["zip"]}})
    result = _run(src, rules_path, tmp_path / "out.csv", "--execute")
    assert result.exit_code == 2
    assert "must be a mapping" in result.output
