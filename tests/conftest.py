from __future__ import annotations

from pathlib import Path

import pytest

from bitcoin_block_archive.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    block_dir = tmp_path / "blocks"
    block_dir.mkdir()

    return Config(
        block_dir=block_dir,
        state_dir=tmp_path / "state",
        s3_endpoint="https://s3.example.invalid",
        s3_profile="testing",
        s3_credentials=tmp_path / "credentials",
        s3_destination="s3://bucket/prefix/",
        keep_latest_files=2,
        stop_bitcoin_on_error=True,
        prune_after_archive=False,
        min_free_space=0,
        bitcoin_cli="bitcoin-cli",
        bitcoin_datadir=tmp_path / "datadir",
    )


def write_block_file(path: Path, headers: list[bytes]) -> None:
    """Write a blk*.dat container holding `headers` as 80-byte blocks."""
    import struct

    payload = b"".join(
        struct.pack("<4sI", b"\xfa\xbf\xb5\xda", len(header)) + header
        for header in headers
    )

    # Bitcoin Core preallocates block files, so pad with zeroes.
    path.write_bytes(payload + b"\x00" * 64)


def fake_header(seed: int) -> bytes:
    return bytes([seed % 256]) * 80
