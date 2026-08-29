"""Checksums of block files."""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 8 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            digest.update(chunk)

    return digest.hexdigest()


def checksum_line(checksum: str, name: str) -> str:
    """`sha256sum`-compatible line stored next to the uploaded object."""
    return f"{checksum}  {name}\n"
