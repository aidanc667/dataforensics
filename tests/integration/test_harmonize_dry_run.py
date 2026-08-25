from click.testing import CliRunner

from datadiligence.cli import main
from datadiligence.hashing import sha256_file


def _write_rules(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "version: 1\n"
        "primary_key: [participant_id]\n"
        "columns: {}\n"
        "missing_values:\n"
        "  smoking_status:\n"
        "    \"99\": Refused\n"
    )
    return rules_path


def test_dry_run_writes_nothing_and_prints_plan(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,smoking_status\n1,99\n2,10\n")
    rules_path = _write_rules(tmp_path)
    before_hash = sha256_file(src)
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert not output_path.exists()
    assert sha256_file(src) == before_hash
    assert "smoking_status" in result.output
    assert "1" in result.output  # rows_affected count for the sentinel rule


def test_output_path_same_as_input_exits_2(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("participant_id,smoking_status\n1,99\n")
    rules_path = _write_rules(tmp_path)

    result = CliRunner().invoke(
        main, ["harmonize", str(src), "--rules", str(rules_path), "--output", str(src)]
    )
    assert result.exit_code == 2


def test_dry_run_shows_footer_stripped_warning_without_execute(tmp_path):
    # Fix 1 regression test: the footer-stripped warning must fire on a
    # plain dry-run invocation (no --execute), not just on --execute --
    # otherwise the README's description of dry-run as a "safe preview
    # before committing" is contradicted by silently-dropped rows never
    # being surfaced in the mode users actually run by default.
    src = tmp_path / "sample.csv"
    src.write_text(
        "participant_id,smoking_status\n"
        "1,99\n"
        "2,10\n"
        '"Query Parameters:"\n'
        '"Group By: participant_id"\n'
    )
    rules_path = _write_rules(tmp_path)
    output_path = tmp_path / "out.csv"

    result = CliRunner().invoke(
        main,
        ["harmonize", str(src), "--rules", str(rules_path), "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert not output_path.exists()
    assert "warning" in result.output.lower()
    assert "footer" in result.output.lower() or "non-data" in result.output.lower()
