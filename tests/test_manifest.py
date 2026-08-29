from __future__ import annotations

import json

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.manifest import MANIFEST_NAME, build_manifest, publish_manifest
from bitcoin_block_archive.state import write_marker
from tests.conftest import FakeClient


def test_manifest_is_contiguous_and_carries_tip_height(config: Config) -> None:
    config.state_dir.mkdir()
    for number, height in ((0, 100), (1, 250)):
        write_marker(
            config,
            config.block_dir / f"blk{number:05d}.dat",
            f"{number + 1:064x}",
            42,
            first_block=(f"{number + 2:064x}", height - 1),
            last_block=(f"{number + 3:064x}", height),
        )

    manifest = build_manifest(config)

    assert manifest["contiguous_from_zero"] is True
    assert manifest["archived_max_height"] == 250


def test_publish_manifest_writes_local_snapshot_and_uploads_it(config: Config) -> None:
    config.state_dir.mkdir()
    write_marker(config, config.block_dir / "blk00000.dat", "a" * 64, 1)
    client = FakeClient()

    publish_manifest(config, client)

    manifest = json.loads((config.state_dir / MANIFEST_NAME).read_text())
    assert manifest["block_files"][0]["file"] == "blk00000.dat"
    assert client.uploads == [
        (MANIFEST_NAME, f"{config.s3_destination.rstrip('/')}/{MANIFEST_NAME}")
    ]


def test_prune_safe_height_overrides_a_file_local_height(config: Config) -> None:
    config.state_dir.mkdir()
    write_marker(
        config,
        config.block_dir / "blk00000.dat",
        "a" * 64,
        1,
        last_block=("b" * 64, 200),
    )

    assert build_manifest(config, archived_max_height=150)["archived_max_height"] == 150
