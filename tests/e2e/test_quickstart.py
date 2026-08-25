from pathlib import Path

from click.testing import CliRunner

from rdh.cli import main

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_quickstart_scan_and_dry_run_harmonize(tmp_path):
    sample = FIXTURES / "sample.csv"
    rules = FIXTURES / "sample_rules.yaml"

    scan_result = CliRunner().invoke(main, ["scan", str(sample), "--rules", str(rules), "--out-dir", str(tmp_path)])
    assert scan_result.exit_code in (0, 1)  # 1 is fine — the fixture plants a real error on purpose

    harmonize_result = CliRunner().invoke(
        main,
        ["harmonize", str(sample), "--rules", str(rules), "--output", str(tmp_path / "out.csv")],
    )
    assert harmonize_result.exit_code == 0
    assert not (tmp_path / "out.csv").exists()  # dry run by default
