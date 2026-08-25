import os
from pathlib import Path

import pytest

from dataforensics.manifest import atomic_write, build_manifest


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


def test_atomic_write_cleans_up_temp_file_on_write_failure(tmp_path, monkeypatch):
    import os as os_module

    target = tmp_path / "out.json"

    original_fdopen = os_module.fdopen

    def failing_fdopen(*args, **kwargs):
        fh = original_fdopen(*args, **kwargs)
        original_write = fh.write

        def failing_write(data):
            raise IOError("simulated write failure")

        fh.write = failing_write
        return fh

    monkeypatch.setattr(os_module, "fdopen", failing_fdopen)

    with pytest.raises(IOError):
        atomic_write(target, "data")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []  # no stray temp file


def test_atomic_write_produces_readable_file_permissions(tmp_path):
    target = tmp_path / "out.json"
    atomic_write(target, "data")
    mode = target.stat().st_mode & 0o777
    assert mode == 0o644


def test_build_manifest_unknown_dependency_version(tmp_path, monkeypatch):
    from importlib.metadata import PackageNotFoundError

    input_file = tmp_path / "input.csv"
    input_file.write_text("a,b\n1,2\n")
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text("version: 1\n")

    def fake_version(name):
        raise PackageNotFoundError(name)

    import dataforensics.manifest as manifest_module

    monkeypatch.setattr(manifest_module, "version", fake_version)

    manifest = build_manifest([input_file], [rules_file])
    assert all(v == "unknown" for v in manifest["dependency_versions"].values())
