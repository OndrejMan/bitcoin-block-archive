from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from bitcoin_s3_archive.blockfile import block_hash, first_block_hash
from bitcoin_s3_archive.errors import ArchiveError
from tests.conftest import fake_header, write_block_file


def test_block_hash_is_reversed_double_sha256() -> None:
    header = fake_header(1)

    expected = hashlib.sha256(hashlib.sha256(header).digest()).digest()

    assert block_hash(header) == expected[::-1].hex()


def test_first_block_hash_reads_only_the_first_record(tmp_path: Path) -> None:
    target = tmp_path / "blk00000.dat"
    write_block_file(target, [fake_header(1), fake_header(2)])

    assert first_block_hash(target) == block_hash(fake_header(1))


def test_empty_file_holds_no_block(tmp_path: Path) -> None:
    target = tmp_path / "blk00000.dat"
    target.write_bytes(b"")

    assert first_block_hash(target) is None


def test_preallocated_file_holds_no_block(tmp_path: Path) -> None:
    target = tmp_path / "blk00000.dat"
    target.write_bytes(b"\x00" * 4096)

    assert first_block_hash(target) is None


def test_garbage_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "blk00000.dat"
    target.write_bytes(b"\xfa\xbf\xb5\xda\xff\xff\xff\xff" + b"x" * 128)

    with pytest.raises(ArchiveError, match="does not look like a block file"):
        first_block_hash(target)


def test_truncated_block_is_rejected(tmp_path: Path) -> None:
    import struct

    target = tmp_path / "blk00000.dat"
    target.write_bytes(
        struct.pack("<4sI", b"\xfa\xbf\xb5\xda", 80) + b"x" * 40
    )

    with pytest.raises(ArchiveError, match="truncated"):
        first_block_hash(target)
