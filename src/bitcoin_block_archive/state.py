"""Per-block markers recording what has already been archived."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from bitcoin_block_archive.config import Config


def marker_path(config: Config, block_file: Path) -> Path:
    return config.state_dir / f"{block_file.name}.json"


def already_archived(config: Config, block_file: Path) -> bool:
    return marker_path(config, block_file).is_file()


def iter_markers(config: Config) -> Iterator[Path]:
    """Yield durable per-block markers in a stable order."""
    if not config.state_dir.is_dir():
        return iter(())
    return iter(sorted(config.state_dir.glob("blk*.dat.json")))


def write_marker(
    config: Config,
    block_file: Path,
    checksum: str,
    size: int,
    *,
    first_block: tuple[str, int] | None = None,
    last_block: tuple[str, int] | None = None,
) -> None:
    marker = marker_path(config, block_file)

    data = {
        "file": block_file.name,
        "size": size,
        "sha256": checksum,
        "destination": config.remote_url(block_file.name),
    }
    if first_block is not None:
        data["first_block"] = {"hash": first_block[0], "height": first_block[1]}
    if last_block is not None:
        data["last_block"] = {"hash": last_block[0], "height": last_block[1]}

    temporary = marker.with_name(f"{marker.name}.tmp")

    temporary.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )

    temporary.replace(marker)
