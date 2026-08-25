from click.testing import CliRunner
from rdh.cli import main


def test_help_lists_subcommands():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.output
    assert "harmonize" in result.output
    assert "report" in result.output


def test_harmonize_with_insufficient_args_exits_2(tmp_path):
    # Neither the single-file path (needs --rules) nor the multi-file
    # crosswalk path (needs --rules-map/--crosswalk/--output-dir) is
    # satisfied, so this is a usage error, not an unimplemented feature.
    f = tmp_path / "somefile.csv"
    f.write_text("a,b\n1,2\n")
    result = CliRunner().invoke(main, ["harmonize", str(f)])
    assert result.exit_code == 2
    assert "invalid arguments" in result.output.lower()
