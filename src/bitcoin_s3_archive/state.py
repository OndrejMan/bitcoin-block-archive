"""Per-block markers recording what has already been archived."""

from __future__ import annotations

import json
from pathlib import Path

from bitcoin_s3_archive.config import Config


def marker_path(config: Config, block_file: Path) -> Path:
    return config.state_dir / f"{block_file.name}.json"


def already_archived(config: Config, block_file: Path) -> bool:
    return marker_path(config, block_file).is_file()


def write_marker(
    config: Config,
    block_file: Path,
    checksum: str,
    size: int,
) -> None:
    marker = marker_path(config, block_file)

    data = {
        "file": block_file.name,
        "size": size,
        "sha256": checksum,
        "destination": config.remote_url(block_file.name),
    }

    temporary = marker.with_name(f"{marker.name}.tmp")

    temporary.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )

    temporary.replace(marker)
