from __future__ import annotations

import hashlib
from pathlib import Path

from bitcoin_s3_archive.hashing import checksum_line, sha256_file


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    payload = b"block data" * 1024
    target = tmp_path / "blk00000.dat"
    target.write_bytes(payload)

    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_checksum_line_is_sha256sum_compatible() -> None:
    assert checksum_line("abc", "blk00000.dat") == "abc  blk00000.dat\n"
