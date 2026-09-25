from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest

from bitcoin_block_archive import archive as archive_module
from bitcoin_block_archive import prune as prune_module
from bitcoin_block_archive.archive import archive
from bitcoin_block_archive.blockfile import block_hash
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.hashing import sha256_file
from bitcoin_block_archive.prune import (
    prune_archived_blocks,
    safe_prune_height,
)
from bitcoin_block_archive.state import file_signature, unarchived_blocks, write_marker
from tests.conftest import FakeClient, fake_header, write_block_file


def make_chain(config: Config, heights: dict[str, int]) -> dict[str, int]:
    """Create blk files whose first block sits at the given height."""
    known = {}

    for name, height in heights.items():
        header = fake_header(height)
        write_block_file(config.block_dir / name, [header])
        known[block_hash(header)] = height

    return known


def mark_first_file(config: Config) -> None:
    path = config.block_dir / "blk00000.dat"
    write_marker(
        config,
        path,
        sha256_file(path),
        path.stat().st_size,
        source_signature=file_signature(path),
    )


@dataclass
class FakeNode:
    """Stand-in for the RPCs `prune` calls, recording prune requests."""

    heights: dict[str, int] = field(default_factory=dict)
    tip: int = 100_000
    pruned: list[int] = field(default_factory=list)


@pytest.fixture
def node(monkeypatch: pytest.MonkeyPatch) -> FakeNode:
    fake = FakeNode()
    monkeypatch.setattr(archive_module, "require_manual_pruning", lambda _: None)
    monkeypatch.setattr(prune_module, "require_manual_pruning", lambda _: None)

    monkeypatch.setattr(
        prune_module,
        "block_height",
        lambda config, block: fake.heights[block],
    )
    monkeypatch.setattr(
        archive_module,
        "block_height",
        lambda config, block: fake.heights[block],
    )
    monkeypatch.setattr(
        prune_module,
        "chain_height",
        lambda config: fake.tip,
    )

    def prune_blockchain(config: Config, height: int) -> int:
        fake.pruned.append(height)
        return height

    monkeypatch.setattr(prune_module, "prune_blockchain", prune_blockchain)

    return fake


def test_unarchived_blocks_ignores_marked_files(config: Config) -> None:
    config.state_dir.mkdir(parents=True)
    make_chain(config, {"blk00000.dat": 1, "blk00001.dat": 2})

    mark_first_file(config)

    assert [path.name for path in unarchived_blocks(config)] == ["blk00001.dat"]


def test_height_stays_below_every_unarchived_file(
    config: Config,
    node: FakeNode,
) -> None:
    config.state_dir.mkdir(parents=True)
    node.heights = make_chain(
        config,
        {"blk00000.dat": 100, "blk00001.dat": 700, "blk00002.dat": 500},
    )

    mark_first_file(config)

    # The lowest unarchived file starts at 500, so 499 is the last height
    # that can never take an unarchived file with it.
    assert safe_prune_height(config) == 499


def test_fully_archived_store_prunes_to_tip(
    config: Config,
    node: FakeNode,
) -> None:
    config.state_dir.mkdir(parents=True)
    node.heights = make_chain(config, {"blk00000.dat": 100})

    mark_first_file(config)

    # Bitcoin Core clamps this to the blocks it keeps around the tip.
    assert safe_prune_height(config) == 100_000


def test_no_block_files_means_no_pruning(
    config: Config,
    node: FakeNode,
) -> None:
    assert safe_prune_height(config) is None


def test_empty_newest_file_does_not_block_pruning(
    config: Config,
    node: FakeNode,
) -> None:
    config.state_dir.mkdir(parents=True)
    node.heights = make_chain(
        config,
        {"blk00000.dat": 100, "blk00001.dat": 900},
    )

    # Bitcoin Core just preallocated the next file.
    (config.block_dir / "blk00002.dat").write_bytes(b"\x00" * 4096)

    mark_first_file(config)

    assert safe_prune_height(config) == 899


def test_nothing_archived_yet_prunes_nothing(
    config: Config,
    node: FakeNode,
) -> None:
    config.state_dir.mkdir(parents=True)
    node.heights = make_chain(config, {"blk00000.dat": 0})

    prune_archived_blocks(config)

    assert node.pruned == []


def test_prune_after_archive_runs_at_end_of_pass(
    config: Config,
    node: FakeNode,
) -> None:
    node.heights = make_chain(
        config,
        {"blk00000.dat": 10, "blk00001.dat": 300, "blk00002.dat": 600},
    )

    pruning = replace(config, prune_after_archive=True)

    archive(pruning, FakeClient())

    # blk00000.dat was archived; the two held-back files start at 300.
    assert node.pruned == [299]


def test_failed_upload_leaves_blocks_unpruned(
    config: Config,
    node: FakeNode,
) -> None:
    node.heights = make_chain(
        config,
        {"blk00000.dat": 10, "blk00001.dat": 300, "blk00002.dat": 600},
    )

    pruning = replace(config, prune_after_archive=True)

    with pytest.raises(RuntimeError):
        archive(pruning, FakeClient(fail_on="blk00000.dat"))

    assert node.pruned == []


def test_archived_file_still_on_disk_does_not_raise_the_height(
    config: Config,
    node: FakeNode,
) -> None:
    """Pruning is asynchronous; markers outlive the files by a while."""
    config.state_dir.mkdir(parents=True)
    node.heights = make_chain(
        config,
        {"blk00000.dat": 10, "blk00001.dat": 300},
    )

    mark_first_file(config)

    assert safe_prune_height(config) == 299


def test_missing_remote_copy_blocks_pruning(config: Config, node: FakeNode) -> None:
    config.state_dir.mkdir()
    node.heights = make_chain(config, {"blk00000.dat": 0, "blk00001.dat": 100})
    mark_first_file(config)
    with pytest.raises(RuntimeError, match="verification refused"):
        prune_archived_blocks(config, FakeClient(fail_on="blk00000.dat"))
    assert node.pruned == []


def test_manual_pruning_is_checked_before_upload(
    config: Config,
    node: FakeNode,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node.heights = make_chain(
        config,
        {
            "blk00000.dat": 0,
            "blk00001.dat": 100,
            "blk00002.dat": 200,
        },
    )

    def reject(_: Config) -> None:
        raise ArchiveError("requires Bitcoin Core prune=1")

    monkeypatch.setattr(archive_module, "require_manual_pruning", reject)
    client = FakeClient()
    with pytest.raises(ArchiveError, match="prune=1"):
        archive(replace(config, prune_after_archive=True), client)
    assert client.uploads == []
    assert node.pruned == []


def test_failed_manifest_upload_blocks_pruning(config: Config, node: FakeNode) -> None:
    node.heights = make_chain(
        config,
        {
            "blk00000.dat": 0,
            "blk00001.dat": 100,
            "blk00002.dat": 200,
        },
    )
    with pytest.raises(RuntimeError, match="upload refused"):
        archive(
            replace(config, prune_after_archive=True),
            FakeClient(fail_on="archive-manifest.json"),
        )
    assert node.pruned == []
