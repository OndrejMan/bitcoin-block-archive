"""Minimal reader for the blk*.dat container format.

Each record is a 4-byte network magic, a little-endian 4-byte payload size
and the serialized block, whose first 80 bytes are the header. Files are
preallocated, so trailing zero bytes mark the end of the real content.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import BinaryIO

from bitcoin_block_archive.constants import BYTES_IN_MEGABYTE
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.hashing import double_sha256

HEADER_SIZE = 80
RECORD_PREFIX_SIZE = 8
XOR_KEY_SIZE = 8
NULL_XOR_KEY = b"\x00" * XOR_KEY_SIZE
PADDING_MAGIC = b"\x00\x00\x00\x00"

# A serialized block cannot approach this; anything larger means the file
# is not a blk*.dat container or is corrupt.
MAX_RECORD_SIZE = 32 * BYTES_IN_MEGABYTE


def validate_block_directory(path: Path) -> None:
    if not path.is_dir():
        raise ArchiveError(f"Block directory does not exist: {path}")
    key_path = path / "xor.dat"
    if key_path.exists():
        key = key_path.read_bytes()
        if key != NULL_XOR_KEY:
            raise ArchiveError(
                f"{path} has an unsupported XOR key; use a datadir initialized "
                "with -blocksxor=0 (changing the flag does not convert old data)"
            )


def block_hash(header: bytes) -> str:
    """Big-endian block hash as printed by Bitcoin Core."""
    if len(header) != HEADER_SIZE:
        raise ArchiveError(
            f"block header must be {HEADER_SIZE} bytes, got {len(header)}"
        )

    return double_sha256(header)[::-1].hex()


def first_block_hash(path: Path) -> str | None:
    """Hash of the first block in `path`, or None when it holds no block."""
    headers = block_headers(path)
    try:
        header = next(headers, None)
        return block_hash(header) if header is not None else None
    finally:
        headers.close()


def last_block_hash(path: Path) -> str | None:
    """Hash of the final complete block in ``path``."""
    last_hash = None
    for header in block_headers(path):
        last_hash = block_hash(header)
    return last_hash


def _read_record_size(file: BinaryIO, path: Path) -> int | None:
    prefix = file.read(RECORD_PREFIX_SIZE)
    if not prefix:
        return None
    if len(prefix) < RECORD_PREFIX_SIZE:
        raise ArchiveError(f"{path} is truncated inside a record prefix")

    magic = prefix[:4]
    size = int.from_bytes(prefix[4:], "little")
    if magic == PADDING_MAGIC and size == 0:
        return None
    if not HEADER_SIZE <= size <= MAX_RECORD_SIZE:
        raise ArchiveError(
            f"{path} does not look like a block file "
            f"(record claims {size} bytes)"
        )
    return size


def _read_block_header(
    file: BinaryIO, path: Path, size: int, file_size: int
) -> bytes:
    header = file.read(HEADER_SIZE)
    if len(header) < HEADER_SIZE:
        raise ArchiveError(f"{path} is truncated inside a block")

    file.seek(size - HEADER_SIZE, 1)
    if file.tell() > file_size:
        raise ArchiveError(f"{path} is truncated inside a block")
    return header


def block_headers(path: Path) -> Generator[bytes, None, None]:
    """Read headers without loading transaction payloads into memory."""
    with path.open("rb") as file:
        file_size = os.fstat(file.fileno()).st_size
        while (size := _read_record_size(file, path)) is not None:
            yield _read_block_header(file, path, size, file_size)
