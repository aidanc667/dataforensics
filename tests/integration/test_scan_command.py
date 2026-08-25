import json
from pathlib import Path

from click.testing import CliRunner

from dataforensics.cli import main
from dataforensics.hashing import sha256_file


def test_scan_writes_dictionary_and_never_modifies_input(tmp_path):
    src = tmp_path / "sample.csv"
    src.write_text("id,age\n001,34\n002,29\n")
    before_hash = sha256_file(src)

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert sha256_file(src) == before_hash

    json_path = tmp_path / "sample.data_dictionary.json"
    md_path = tmp_path / "sample.data_dictionary.md"
    assert json_path.exists()
    assert md_path.exists()

    payload = json.loads(json_path.read_text())
    assert "age" in payload


def test_scan_warns_on_stderr_when_footer_stripping_drops_lines(tmp_path):
    # strip_footer's field-count heuristic is not CSV-quote-aware (see
    # ingest.strip_footer's docs): two consecutive genuine data rows
    # containing a quoted delimiter (a comma inside quotes, in a
    # comma-delimited file) raise the raw comma count above the header's,
    # so strip_footer misclassifies both as a footer block and drops them.
    # This is a known, documented limitation (see README's "Known
    # limitations") -- the fix under test isn't preventing the
    # misclassification, only making sure it's no longer silent.
    src = tmp_path / "clinics.csv"
    src.write_text(
        "name,age\n"
        "Bob,34\n"
        '"Delta Clinic, North",40\n'
        '"Acme Labs, South",50\n'
    )

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "2" in result.output  # 2 lines stripped
    assert "clinics.csv" in result.output

    # The misclassification itself is real: both genuine data rows are gone
    # from the dictionary's row-derived stats, confirming this reproduces
    # the documented limitation rather than testing a scenario that can't
    # happen.
    payload = json.loads((tmp_path / "clinics.data_dictionary.json").read_text())
    assert payload["name"]["null_count"] == 0
    assert payload["age"]["unique_count"] == 1  # only "34" (Bob) survived


def test_scan_prints_no_warning_when_nothing_stripped(tmp_path):
    src = tmp_path / "clean.csv"
    src.write_text("id,age\n001,34\n002,29\n")

    result = CliRunner().invoke(main, ["scan", str(src), "--out-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Warning" not in result.output
