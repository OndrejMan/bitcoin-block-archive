from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_block_archive import archive as archive_module
from bitcoin_block_archive.archive import archive, archive_block
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.hashing import sha256_file
from bitcoin_block_archive.state import (
    already_archived,
    file_signature,
    marker_path,
    write_marker,
)
from tests.conftest import FakeClient, fake_header, write_block_file


@pytest.fixture
def block(config: Config) -> Path:
    config.state_dir.mkdir()
    path = config.block_dir / "blk00000.dat"
    write_block_file(path, [fake_header(1)])
    return path


def mark(config: Config, block: Path) -> None:
    write_marker(
        config,
        block,
        sha256_file(block),
        block.stat().st_size,
        source_signature=file_signature(block),
    )


@pytest.mark.parametrize("setting", ["s3_destination", "s3_endpoint"])
def test_markers_cannot_be_reused_for_another_destination(
    config: Config,
    block: Path,
    setting: str,
) -> None:
    mark(config, block)
    changed = (
        replace(config, s3_destination="s3://other/blocks")
        if setting == "s3_destination"
        else replace(config, s3_endpoint="https://other.invalid")
    )
    with pytest.raises(ArchiveError, match="destination|endpoint"):
        already_archived(changed, block)


def test_modified_file_is_no_longer_archived(config: Config, block: Path) -> None:
    mark(config, block)
    # Same length: preallocated blk files need not grow when Core writes to them.
    write_block_file(block, [fake_header(2)])
    assert not already_archived(config, block)


def test_corrupt_marker_is_not_evidence_of_an_upload(
    config: Config,
    block: Path,
) -> None:
    marker_path(config, block).write_text("{}")
    with pytest.raises(ArchiveError):
        already_archived(config, block)


def test_mutation_during_upload_never_creates_a_marker(
    config: Config,
    block: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(archive_module, "block_height", lambda *_: 1)

    class ChangingClient(FakeClient):
        def upload(self, source: Path, destination: str) -> None:
            super().upload(source, destination)
            if source == block:
                write_block_file(block, [fake_header(2)])

    with pytest.raises(ArchiveError, match="changed"):
        archive_block(config, ChangingClient(), block)
    assert not marker_path(config, block).exists()


def test_rpc_failure_does_not_upload_a_block(
    config: Config,
    block: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args: object) -> int:
        raise ArchiveError("RPC unavailable")

    monkeypatch.setattr(archive_module, "block_height", unavailable)
    client = FakeClient()
    with pytest.raises(ArchiveError, match="RPC unavailable"):
        archive_block(config, client, block)
    assert client.uploads == []


def test_xor_blocks_are_rejected_before_any_upload(config: Config) -> None:
    (config.block_dir / "xor.dat").write_bytes(b"\x01" * 8)
    client = FakeClient()
    with pytest.raises(ArchiveError, match="XOR"):
        archive(config, client)
    assert client.uploads == []


def test_missing_block_directory_does_not_publish_an_empty_archive(
    config: Config,
) -> None:
    client = FakeClient()
    with pytest.raises(ArchiveError, match="directory"):
        archive(replace(config, block_dir=config.block_dir / "missing"), client)
    assert client.uploads == []


def test_marker_without_endpoint_is_rejected(
    config: Config,
    block: Path,
) -> None:
    mark(config, block)
    path = marker_path(config, block)
    payload = json.loads(path.read_text())
    payload.pop("endpoint", None)
    path.write_text(json.dumps(payload))
    with pytest.raises(ArchiveError, match="endpoint"):
        already_archived(config, block)
