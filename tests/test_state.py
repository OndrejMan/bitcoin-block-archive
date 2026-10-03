from __future__ import annotations

import json
import stat
import time
from pathlib import Path

import pytest

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
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

    write_marker(
        config,
        block_file,
        "d" * 64,
        42,
        height_ranges=((10, 12), (20, 20)),
        source_signature=signature,
    )

    assert already_archived(config, block_file)

    data = json.loads(marker_path(config, block_file).read_text())

    assert data == {
        "schema_version": 1,
        "file": "blk00000.dat",
        "size": 42,
        "sha256": "d" * 64,
        "height_ranges": [[10, 12], [20, 20]],
        "destination": "s3://bucket/prefix/blk00000.dat",
        "endpoint": "https://s3.example.invalid",
        "mtime_ns": signature.mtime_ns,
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
        height_ranges=((1, 1),),
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
        height_ranges=((0, 1), (4, 4)),
        source_signature=file_signature(block),
    )
    return block


def test_typed_marker_preserves_json_and_source_metadata(
    config: Config, marked_file: Path
) -> None:
    path = marker_path(config, marked_file)
    marker = read_marker(config, path)
    assert marker.source_signature == file_signature(marked_file)
    assert marker.height_ranges == ((0, 1), (4, 4))
    assert marker.sidecar_json() == {
        "schema_version": 1,
        "file": "blk00000.dat",
        "size": 5,
        "sha256": "a" * 64,
        "height_ranges": [[0, 1], [4, 4]],
    }
    assert marker.to_json() == json.loads(path.read_text())


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", True),
        ("mtime_ns", "123"),
        ("schema_version", 2),
        ("schema_version", True),
        ("height_ranges", []),
        ("height_ranges", [[0, True]]),
        ("height_ranges", [[2, 1]]),
        ("height_ranges", [[0, 1], [2, 3]]),
        ("height_ranges", [[4, 5], [1, 2]]),
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


def test_metadata_change_keeps_the_marker(config: Config, marked_file: Path) -> None:
    # The Bitcoin Core image chowns its datadir on every start; chmod stands in
    # for it here, since both update ctime but not mtime or content.
    before = marked_file.stat().st_ctime_ns
    deadline = time.monotonic() + 2
    while marked_file.stat().st_ctime_ns == before:
        assert time.monotonic() < deadline, "ctime did not advance"
        marked_file.chmod(marked_file.stat().st_mode ^ stat.S_IXUSR)
    assert already_archived(config, marked_file)


def test_marker_with_a_ctime_from_an_older_version_is_accepted(
    config: Config, marked_file: Path
) -> None:
    path = marker_path(config, marked_file)
    payload = json.loads(path.read_text())
    payload["ctime_ns"] = 1
    path.write_text(json.dumps(payload))
    assert already_archived(config, marked_file)


def test_marker_without_source_signature_is_rejected(
    config: Config, marked_file: Path
) -> None:
    path = marker_path(config, marked_file)
    payload = json.loads(path.read_text())
    del payload["mtime_ns"]
    path.write_text(json.dumps(payload))
    with pytest.raises(ArchiveError, match="mtime_ns"):
        already_archived(config, marked_file)
