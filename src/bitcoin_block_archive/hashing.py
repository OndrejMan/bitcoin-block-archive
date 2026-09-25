"""Checksums of block files."""

from __future__ import annotations

import hashlib
from pathlib import Path

from bitcoin_block_archive.constants import BYTES_IN_MEGABYTE

CHUNK_SIZE = 8 * BYTES_IN_MEGABYTE


def double_sha256(data: bytes) -> bytes:
    """Raw double SHA-256 digest."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            digest.update(chunk)

    return digest.hexdigest()


def checksum_line(checksum: str, name: str) -> str:
    """`sha256sum`-compatible line stored next to the uploaded object."""
    return f"{checksum}  {name}\n"
