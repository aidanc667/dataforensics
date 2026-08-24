from click.testing import CliRunner
from rdh.cli import main


def test_help_lists_subcommands():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.output
    assert "harmonize" in result.output
    assert "report" in result.output


def test_scan_stub_exits_3(tmp_path):
    f = tmp_path / "somefile.csv"
    f.write_text("a,b\n1,2\n")
    result = CliRunner().invoke(main, ["scan", str(f)])
    assert result.exit_code == 3
    assert "not implemented" in result.output.lower()
