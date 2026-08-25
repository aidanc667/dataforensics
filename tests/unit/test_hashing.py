import hashlib
from pathlib import Path

from datadiligence.hashing import sha256_file


def test_sha256_file_matches_stdlib(tmp_path):
    f = tmp_path / "sample.txt"
    content = b"a,b,c\n1,2,3\n"
    f.write_bytes(content)

    assert sha256_file(f) == hashlib.sha256(content).hexdigest()


def test_sha256_file_handles_large_content(tmp_path):
    f = tmp_path / "big.txt"
    content = b"x" * (5 * 1024 * 1024 + 7)  # not a multiple of the chunk size
    f.write_bytes(content)

    assert sha256_file(f) == hashlib.sha256(content).hexdigest()
