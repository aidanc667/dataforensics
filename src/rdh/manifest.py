import os
import platform
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from rdh import __version__
from rdh.hashing import sha256_file

_DIRECT_DEPENDENCIES = ["polars", "click", "pyyaml", "charset-normalizer", "rapidfuzz"]


def _dependency_versions() -> dict:
    versions = {}
    for name in _DIRECT_DEPENDENCIES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "unknown"
    return versions


def build_manifest(input_paths: list[Path], schema_paths: list[Path], provenance: dict | None = None) -> dict:
    return {
        "tool_version": __version__,
        "python_version": platform.python_version(),
        "dependency_versions": _dependency_versions(),
        "input_sha256": [sha256_file(p) for p in input_paths],
        "schema_sha256": [sha256_file(p) for p in schema_paths],
        "run_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance,
        "mutations": [],
    }


def atomic_write(path: Path, content: str) -> None:
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise
