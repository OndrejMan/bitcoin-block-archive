from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_block_archive import archive as archive_module
from bitcoin_block_archive.archive import (
    archive,
    archive_block,
    find_archivable_blocks,
)
from bitcoin_block_archive.blockfile import block_hash
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.state import already_archived, marker_path, read_marker
from tests.conftest import FakeClient, fake_header, write_block_file


def make_blocks(config: Config, count: int) -> list[Path]:
    blocks = []

    for index in range(count):
        block = config.block_dir / f"blk{index:05d}.dat"
        write_block_file(block, [fake_header(index * 2), fake_header(index * 2 + 1)])
        blocks.append(block)

    return blocks


@pytest.fixture(autouse=True)
def archive_block_heights(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        archive_module,
        "block_height",
        lambda _config, block: int(block[:2], 16),
    )


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


def test_archive_uploads_block_and_metadata(
    config: Config,
    client: FakeClient,
) -> None:
    make_blocks(config, 3)

    archive(config, client)

    assert client.uploads == [
        ("blk00000.dat", "s3://bucket/prefix/blk00000.dat"),
        (client.uploads[1][0], "s3://bucket/prefix/blk00000.dat.json"),
    ]
    assert already_archived(config, config.block_dir / "blk00000.dat")


def test_archive_is_idempotent(config: Config, client: FakeClient) -> None:
    make_blocks(config, 3)

    archive(config, client)
    uploads_after_first = list(client.uploads)

    archive(config, client)

    assert client.uploads == uploads_after_first


def test_no_marker_when_metadata_upload_fails(config: Config) -> None:
    make_blocks(config, 3)
    failing = FakeClient(fail_on=".dat.json")

    with pytest.raises(RuntimeError):
        archive(config, failing)

    assert not already_archived(config, config.block_dir / "blk00000.dat")


def test_metadata_sidecar_records_compact_height_ranges(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config.state_dir.mkdir()
    first_root = fake_header(10)
    first_child = fake_header(11)
    first_child = (
        first_child[:4] + bytes.fromhex(block_hash(first_root))[::-1] + first_child[36:]
    )
    second_root = fake_header(20)
    second_child = fake_header(21)
    second_child = (
        second_child[:4]
        + bytes.fromhex(block_hash(second_root))[::-1]
        + second_child[36:]
    )
    block = config.block_dir / "blk00000.dat"
    write_block_file(block, [second_child, first_root, first_child, second_root])

    roots = {
        block_hash(first_root): 100,
        block_hash(second_root): 200,
    }
    calls: list[str] = []

    def height(_: Config, digest: str) -> int:
        calls.append(digest)
        return roots[digest]

    monkeypatch.setattr(archive_module, "block_height", height)

    class CapturingClient(FakeClient):
        metadata: object = None

        def upload(self, source: Path, destination: str) -> None:
            if destination.endswith(".dat.json"):
                self.metadata = json.loads(source.read_text())
            super().upload(source, destination)

    client = CapturingClient()
    archive_block(config, client, block)

    marker = read_marker(config, marker_path(config, block))
    assert marker.height_ranges == ((100, 101), (200, 201))
    assert client.metadata == marker.sidecar_json()
    assert sorted(calls) == sorted(roots)


@pytest.mark.parametrize("seed", range(8))
def test_ranges_handle_branches_duplicates_gaps_and_arbitrary_order(
    config: Config, monkeypatch: pytest.MonkeyPatch, seed: int
) -> None:
    headers: list[bytes] = []
    roots: dict[str, int] = {}

    def add_header(number: int, parent: int | None, height: int) -> None:
        header = fake_header(number)
        if parent is None:
            roots[block_hash(header)] = height
        else:
            header = (
                header[:4]
                + bytes.fromhex(block_hash(headers[parent]))[::-1]
                + header[36:]
            )
        headers.append(header)

    add_header(1, None, 0)
    add_header(2, 0, 1)
    add_header(3, 1, 2)
    add_header(4, 0, 1)
    add_header(5, 3, 2)
    add_header(6, 4, 3)
    add_header(7, None, 10)
    add_header(8, 6, 11)
    add_header(9, None, 20)
    headers.extend([headers[2], headers[6]])
    random.Random(seed).shuffle(headers)
    block = config.block_dir / "blk00000.dat"
    write_block_file(block, headers)
    calls: list[str] = []

    def height(_: Config, digest: str) -> int:
        calls.append(digest)
        return roots[digest]

    monkeypatch.setattr(archive_module, "block_height", height)
    assert archive_module._block_height_ranges(config, block) == (
        (0, 3),
        (10, 11),
        (20, 20),
    )
    assert sorted(calls) == sorted(roots)


def test_retry_after_failed_metadata_upload(config: Config) -> None:
    make_blocks(config, 3)
    with pytest.raises(RuntimeError):
        archive(config, FakeClient(fail_on=".dat.json"))
    assert list(config.state_dir.glob("*.json")) == []

    client = FakeClient()
    archive(config, client)
    assert already_archived(config, config.block_dir / "blk00000.dat")
    assert len(client.uploads) == 2


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

    from bitcoin_block_archive.locking import exclusive_lock

    with exclusive_lock(config.lock_path) as acquired:
        assert acquired

        archive(config, client)

    assert client.uploads == []
