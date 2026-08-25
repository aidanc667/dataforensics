import json
from pathlib import Path

from click.testing import CliRunner

from rdh.cli import main
from rdh.hashing import sha256_file


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
