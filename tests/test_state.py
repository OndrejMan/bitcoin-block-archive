from __future__ import annotations

import json
from pathlib import Path

import pytest

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.models import BlockReference
from bitcoin_block_archive.state import (
    already_archived,
    file_signature,
    marker_path,
    read_marker,
    write_marker,
)


def test_marker_roundtrip(config: Config) -> None:
    config.state_dir.mkdir(parents=True)
    block_file = config.block_dir / "blk00000.dat"
    block_file.write_bytes(b"x" * 42)
    signature = file_signature(block_file)

    assert not already_archived(config, block_file)

    write_marker(config, block_file, "d" * 64, 42, source_signature=signature)

    assert already_archived(config, block_file)

    data = json.loads(marker_path(config, block_file).read_text())

    assert data == {
        "file": "blk00000.dat",
        "size": 42,
        "sha256": "d" * 64,
        "destination": "s3://bucket/prefix/blk00000.dat",
        "endpoint": "https://s3.example.invalid",
        "mtime_ns": signature.mtime_ns,
        "ctime_ns": signature.ctime_ns,
    }


def test_write_marker_leaves_no_temporary_file(config: Config) -> None:
    config.state_dir.mkdir(parents=True)
    block_file = config.block_dir / "blk00001.dat"
    block_file.write_bytes(b"x")

    write_marker(
        config,
        block_file,
        "c" * 64,
        1,
        source_signature=file_signature(block_file),
    )

    assert [path.name for path in config.state_dir.iterdir()] == ["blk00001.dat.json"]


@pytest.fixture
def marked_file(config: Config) -> Path:
    config.state_dir.mkdir()
    block = config.block_dir / "blk00000.dat"
    block.write_bytes(b"block")
    write_marker(
        config,
        block,
        "a" * 64,
        5,
        first_block=BlockReference("b" * 64, 0),
        last_block=BlockReference("c" * 64, 1),
        source_signature=file_signature(block),
    )
    return block


def test_typed_marker_preserves_json_and_source_metadata(
    config: Config, marked_file: Path
) -> None:
    path = marker_path(config, marked_file)
    marker = read_marker(config, path)
    assert marker.source_signature == file_signature(marked_file)
    assert marker.first_block == BlockReference("b" * 64, 0)
    assert marker.last_block == BlockReference("c" * 64, 1)
    assert marker.to_json() == json.loads(path.read_text())


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", True),
        ("mtime_ns", "123"),
        ("ctime_ns", False),
        ("first_block", []),
        ("first_block", {"hash": "b" * 64, "height": True}),
        ("last_block", {"hash": "invalid", "height": 1}),
        ("last_block", {"hash": "c" * 64, "height": -1}),
    ],
)
def test_invalid_metadata_is_rejected_when_reading_marker(
    config: Config, marked_file: Path, field: str, value: object
) -> None:
    path = marker_path(config, marked_file)
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ArchiveError):
        read_marker(config, path)


def test_partial_source_signature_is_rejected(
    config: Config, marked_file: Path
) -> None:
    path = marker_path(config, marked_file)
    payload = json.loads(path.read_text())
    del payload["ctime_ns"]
    path.write_text(json.dumps(payload))
    with pytest.raises(ArchiveError, match="ctime_ns"):
        read_marker(config, path)


def test_marker_without_source_signature_is_rejected(
    config: Config, marked_file: Path
) -> None:
    path = marker_path(config, marked_file)
    payload = json.loads(path.read_text())
    del payload["mtime_ns"], payload["ctime_ns"]
    path.write_text(json.dumps(payload))
    with pytest.raises(ArchiveError, match="mtime_ns"):
        already_archived(config, marked_file)
