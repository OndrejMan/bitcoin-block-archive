"""Publish a verified inventory of archived Bitcoin block files.

The per-file state markers protect pruning.  This manifest is the portable
counterpart consumed by a restore job: it describes exactly which immutable
``blk*.dat`` objects form the archived snapshot and the checksum expected for
each one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.state import iter_markers

MANIFEST_NAME = "archive-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
_BLOCK_NAME = re.compile(r"^blk([0-9]{5})\.dat$")


class Uploader(Protocol):
    """Minimal upload surface shared with the archival pass."""

    def upload(self, source: Path, destination: str) -> None: ...


def _marker_entry(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArchiveError(f"Cannot read archive marker {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ArchiveError(f"Archive marker {path} must contain an object")

    name = payload.get("file")
    size = payload.get("size")
    checksum = payload.get("sha256")
    match = _BLOCK_NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or not isinstance(size, int) or size < 0:
        raise ArchiveError(f"Archive marker {path} has invalid file or size")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ArchiveError(f"Archive marker {path} has invalid SHA-256")

    entry: dict[str, object] = {"file": name, "size": size, "sha256": checksum}
    for key in ("first_block", "last_block"):
        block = payload.get(key)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise ArchiveError(f"Archive marker {path} has invalid {key}")
        block_hash, height = block.get("hash"), block.get("height")
        if (
            not isinstance(block_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", block_hash)
            or not isinstance(height, int)
            or height < 0
        ):
            raise ArchiveError(f"Archive marker {path} has invalid {key}")
        entry[key] = {"hash": block_hash, "height": height}
    return entry


def build_manifest(
    config: Config, *, archived_max_height: int | None = None
) -> dict[str, object]:
    """Build a strict, deterministic inventory from local archive markers."""
    entries = [_marker_entry(path) for path in iter_markers(config)]
    entries.sort(key=lambda entry: str(entry["file"]))
    names = [entry["file"] for entry in entries]
    if len(names) != len(set(names)):
        raise ArchiveError("Archive markers contain duplicate block-file names")

    sequence = [_BLOCK_NAME.fullmatch(str(name)) for name in names]
    contiguous_from_zero = all(
        match is not None and int(match.group(1)) == index
        for index, match in enumerate(sequence)
    )
    heights: list[int] = []
    for entry in entries:
        last_block = entry.get("last_block")
        if isinstance(last_block, dict) and isinstance(last_block.get("height"), int):
            heights.append(last_block["height"])
    recorded_max_height = max(heights) if heights else None
    if archived_max_height is not None:
        if archived_max_height < 0:
            raise ArchiveError("Archive maximum height must not be negative")
        recorded_max_height = archived_max_height
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "destination": config.s3_destination.rstrip("/"),
        "contiguous_from_zero": contiguous_from_zero,
        # The archive pass supplies the prune-safe lower bound. Unlike a
        # highest block seen in a blk file, this proves every active-chain
        # block through the height is safely archived.
        "archived_max_height": recorded_max_height,
        "block_files": entries,
    }


def publish_manifest(
    config: Config, client: Uploader, *, archived_max_height: int | None = None
) -> None:
    """Atomically write the local manifest, then make it the S3 snapshot."""
    manifest = build_manifest(config, archived_max_height=archived_max_height)
    target = config.state_dir / MANIFEST_NAME
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(target)
    client.upload(target, config.remote_url(MANIFEST_NAME))
