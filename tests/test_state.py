from __future__ import annotations

import json

from bitcoin_s3_archive.config import Config
from bitcoin_s3_archive.state import (
    already_archived,
    marker_path,
    write_marker,
)


def test_marker_roundtrip(config: Config) -> None:
    config.state_dir.mkdir(parents=True)
    block_file = config.block_dir / "blk00000.dat"

    assert not already_archived(config, block_file)

    write_marker(config, block_file, "deadbeef", 42)

    assert already_archived(config, block_file)

    data = json.loads(marker_path(config, block_file).read_text())

    assert data == {
        "file": "blk00000.dat",
        "size": 42,
        "sha256": "deadbeef",
        "destination": "s3://bucket/prefix/blk00000.dat",
    }


def test_write_marker_leaves_no_temporary_file(config: Config) -> None:
    config.state_dir.mkdir(parents=True)
    block_file = config.block_dir / "blk00001.dat"

    write_marker(config, block_file, "cafe", 1)

    assert [path.name for path in config.state_dir.iterdir()] == [
        "blk00001.dat.json"
    ]
