"""Publish a verified inventory of archived Bitcoin block files.

The per-file state markers protect pruning.  This manifest is the portable
counterpart consumed by a restore job: it describes exactly which immutable
``blk*.dat`` objects form the archived snapshot and the checksum expected for
each one.
"""

from __future__ import annotations

import json
from pathlib import Path

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.models import ArchiveEntryJSON, ArchiveManifestJSON
from bitcoin_block_archive.s3 import Uploader
from bitcoin_block_archive.state import (
    BLOCK_NAME,
    iter_markers,
    marker_matches_file,
    read_marker,
)

MANIFEST_NAME = "archive-manifest.json"
MANIFEST_SCHEMA_VERSION = 1


def _marker_entry(config: Config, path: Path) -> ArchiveEntryJSON:
    marker = read_marker(config, path)
    if not marker_matches_file(marker, config.block_dir / marker.file):
        raise ArchiveError(
            f"Archived file {marker.file} changed; archive it again first"
        )
    return marker.entry_json()


def build_manifest(
    config: Config, *, archived_max_height: int | None = None
) -> ArchiveManifestJSON:
    """Build a strict, deterministic inventory from local archive markers."""
    entries = [_marker_entry(config, path) for path in iter_markers(config)]
    entries.sort(key=lambda entry: entry["file"])
    names = [entry["file"] for entry in entries]
    if len(names) != len(set(names)):
        raise ArchiveError("Archive markers contain duplicate block-file names")

    sequence = [BLOCK_NAME.fullmatch(name) for name in names]
    contiguous_from_zero = bool(entries) and all(
        match is not None and int(match.group(1)) == index
        for index, match in enumerate(sequence)
    )
    recorded_max_height = None
    if archived_max_height is not None:
        if archived_max_height < 0:
            raise ArchiveError("Archive maximum height must not be negative")
        if contiguous_from_zero:
            recorded_max_height = archived_max_height
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "destination": config.s3_destination.rstrip("/"),
        "contiguous_from_zero": contiguous_from_zero,
        # Coverage is calculated separately from the file-safe prune height.
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
