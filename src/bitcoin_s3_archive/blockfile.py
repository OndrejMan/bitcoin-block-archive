"""Minimal reader for the blk*.dat container format.

Each record is a 4-byte network magic, a little-endian 4-byte payload size
and the serialized block, whose first 80 bytes are the header. Files are
preallocated, so trailing zero bytes mark the end of the real content.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from bitcoin_s3_archive.errors import ArchiveError

HEADER_SIZE = 80
RECORD_PREFIX = struct.Struct("<4sI")
PADDING_MAGIC = b"\x00\x00\x00\x00"

# A serialized block cannot approach this; anything larger means the file
# is not a blk*.dat container or is corrupt.
MAX_RECORD_SIZE = 32 * 1024 * 1024


def block_hash(header: bytes) -> str:
    """Big-endian block hash as printed by Bitcoin Core."""
    if len(header) != HEADER_SIZE:
        raise ArchiveError(
            f"block header must be {HEADER_SIZE} bytes, got {len(header)}"
        )

    digest = hashlib.sha256(hashlib.sha256(header).digest()).digest()

    return digest[::-1].hex()


def first_block_hash(path: Path) -> str | None:
    """Hash of the first block in `path`, or None when it holds no block."""
    with path.open("rb") as file:
        prefix = file.read(RECORD_PREFIX.size)

        if len(prefix) < RECORD_PREFIX.size:
            return None

        magic, size = RECORD_PREFIX.unpack(prefix)

        if magic == PADDING_MAGIC:
            return None

        if not HEADER_SIZE <= size <= MAX_RECORD_SIZE:
            raise ArchiveError(
                f"{path} does not look like a block file "
                f"(first record claims {size} bytes)"
            )

        header = file.read(HEADER_SIZE)

        if len(header) < HEADER_SIZE:
            raise ArchiveError(f"{path} is truncated inside its first block")

        return block_hash(header)
