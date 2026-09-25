from __future__ import annotations

import hashlib
from pathlib import Path

from bitcoin_block_archive.hashing import sha256_file


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    payload = b"block data" * 1024
    target = tmp_path / "blk00000.dat"
    target.write_bytes(payload)

    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()
