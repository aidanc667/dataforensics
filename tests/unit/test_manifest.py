import os
from pathlib import Path

from rdh.manifest import atomic_write, build_manifest


def test_build_manifest_has_required_fields(tmp_path):
    input_file = tmp_path / "input.csv"
    input_file.write_text("a,b\n1,2\n")
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("version: 1\n")

    manifest = build_manifest([input_file], [rules_file])

    for field in ("tool_version", "python_version", "dependency_versions", "run_id", "timestamp_utc", "mutations"):
        assert field in manifest

    assert len(manifest["input_sha256"]) == 1
    assert len(manifest["schema_sha256"]) == 1
    assert manifest["mutations"] == []
    assert manifest["provenance"] is None


def test_build_manifest_records_provenance_only_if_given(tmp_path):
    input_file = tmp_path / "input.csv"
    input_file.write_text("a,b\n1,2\n")
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("version: 1\n")

    manifest = build_manifest(
        [input_file], [rules_file], provenance={"source": "CDC", "dataset": "WONDER"}
    )
    assert manifest["provenance"] == {"source": "CDC", "dataset": "WONDER"}


def test_atomic_write_creates_file_with_content(tmp_path):
    target = tmp_path / "out.json"
    atomic_write(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'


def test_atomic_write_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "out.json"
    atomic_write(target, "data")
    remaining = list(tmp_path.iterdir())
    assert remaining == [target]
