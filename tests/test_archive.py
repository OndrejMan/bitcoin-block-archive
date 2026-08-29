from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_s3_archive import archive as archive_module
from bitcoin_s3_archive.archive import (
    archive,
    archive_block,
    find_archivable_blocks,
)
from bitcoin_s3_archive.config import Config
from bitcoin_s3_archive.errors import ArchiveError
from bitcoin_s3_archive.state import already_archived
from tests.conftest import FakeClient


def make_blocks(config: Config, count: int) -> list[Path]:
    blocks = []

    for index in range(count):
        block = config.block_dir / f"blk{index:05d}.dat"
        block.write_bytes(f"block-{index}".encode())
        blocks.append(block)

    return blocks


def test_keeps_newest_files_back(config: Config) -> None:
    make_blocks(config, 5)

    names = [path.name for path in find_archivable_blocks(config)]

    assert names == ["blk00000.dat", "blk00001.dat", "blk00002.dat"]


def test_nothing_to_archive_when_only_newest_exist(config: Config) -> None:
    make_blocks(config, 2)

    assert find_archivable_blocks(config) == []


def test_keep_zero_archives_everything(config: Config) -> None:
    make_blocks(config, 3)

    relaxed = replace(config, keep_latest_files=0)

    assert len(find_archivable_blocks(relaxed)) == 3


def test_archive_uploads_block_and_checksum(
    config: Config,
    client: FakeClient,
) -> None:
    make_blocks(config, 3)

    archive(config, client)

    assert client.uploads == [
        ("blk00000.dat", "s3://bucket/prefix/blk00000.dat"),
        (client.uploads[1][0], "s3://bucket/prefix/blk00000.dat.sha256"),
    ]
    assert already_archived(config, config.block_dir / "blk00000.dat")


def test_archive_is_idempotent(config: Config, client: FakeClient) -> None:
    make_blocks(config, 3)

    archive(config, client)
    uploads_after_first = len(client.uploads)

    archive(config, client)

    assert len(client.uploads) == uploads_after_first


def test_no_marker_when_upload_fails(config: Config) -> None:
    make_blocks(config, 3)
    failing = FakeClient(fail_on=".sha256")

    with pytest.raises(RuntimeError):
        archive(config, failing)

    assert not already_archived(config, config.block_dir / "blk00000.dat")


def test_changed_block_is_not_archived(
    config: Config,
    client: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config.state_dir.mkdir(parents=True)
    block = make_blocks(config, 1)[0]

    def growing_hash(path: Path) -> str:
        path.write_bytes(b"appended while hashing")
        return "irrelevant"

    monkeypatch.setattr(archive_module, "sha256_file", growing_hash)

    with pytest.raises(ArchiveError, match="changed while being archived"):
        archive_block(config, client, block)

    assert client.uploads == []


def test_missing_block_reports_disappearance(
    config: Config,
    client: FakeClient,
) -> None:
    config.state_dir.mkdir(parents=True)

    with pytest.raises(ArchiveError, match="disappeared"):
        archive_block(config, client, config.block_dir / "blk00000.dat")


def test_second_process_backs_off(config: Config, client: FakeClient) -> None:
    make_blocks(config, 3)
    config.state_dir.mkdir(parents=True, exist_ok=True)

    from bitcoin_s3_archive.locking import exclusive_lock

    with exclusive_lock(config.lock_path) as acquired:
        assert acquired

        archive(config, client)

    assert client.uploads == []
