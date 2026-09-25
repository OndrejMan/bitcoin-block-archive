"""Selection and archival of completed block files."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bitcoin_block_archive.bitcoin import block_height, require_manual_pruning
from bitcoin_block_archive.blockfile import block_headers, validate_block_directory
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.hashing import sha256_file
from bitcoin_block_archive.heights import block_height_ranges
from bitcoin_block_archive.locking import exclusive_lock
from bitcoin_block_archive.logging_setup import LOG
from bitcoin_block_archive.models import ArchiveMarker, FileSignature, HeightRange
from bitcoin_block_archive.prune import prune_archived_blocks
from bitcoin_block_archive.s3 import S5cmdClient, Uploader
from bitcoin_block_archive.state import already_archived, file_signature, write_marker

BLOCK_FILE_GLOB = "blk*.dat"


@dataclass(frozen=True)
class _ArchiveMetadata:
    checksum: str
    size: int
    height_ranges: tuple[HeightRange, ...]


def find_archivable_blocks(config: Config) -> list[Path]:
    """Completed block files, newest `keep_latest_files` held back."""
    block_files = sorted(config.block_dir.glob(BLOCK_FILE_GLOB))

    if len(block_files) <= config.keep_latest_files:
        return []

    if config.keep_latest_files <= 0:
        return block_files

    return block_files[: -config.keep_latest_files]


def _source_signature(block_file: Path, when: str) -> FileSignature:
    signature: FileSignature
    try:
        signature = file_signature(block_file)
    except FileNotFoundError as error:
        raise ArchiveError(f"{block_file} disappeared {when}") from error

    return signature


def _ensure_source_unchanged(
    block_file: Path,
    before: FileSignature,
    when: str,
) -> FileSignature:
    current = _source_signature(block_file, when)
    if before != current:
        raise ArchiveError(f"{block_file} changed while being archived")
    return current


def _checksum_unchanged(block_file: Path, before: FileSignature) -> str:
    checksum = sha256_file(block_file)
    _ensure_source_unchanged(block_file, before, "while calculating checksum")
    return checksum


def _block_height_ranges(
    config: Config,
    block_file: Path,
) -> tuple[HeightRange, ...]:
    return block_height_ranges(
        block_headers(block_file),
        lambda digest: block_height(config, digest),
        str(block_file),
    )


def _archive_metadata(
    config: Config,
    block_file: Path,
    before: FileSignature,
) -> _ArchiveMetadata:
    checksum = _checksum_unchanged(block_file, before)
    height_ranges = _block_height_ranges(config, block_file)
    after = _ensure_source_unchanged(
        block_file,
        before,
        "while reading block metadata",
    )
    return _ArchiveMetadata(
        checksum=checksum,
        size=after.size,
        height_ranges=height_ranges,
    )


def _upload_metadata_sidecar(
    config: Config,
    client: Uploader,
    marker: ArchiveMarker,
    remote_block: str,
) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix=f"{marker.file}.",
        suffix=".json",
        dir=config.state_dir,
        delete=False,
    ) as metadata_file:
        metadata_file.write(
            json.dumps(marker.sidecar_json(), indent=2, sort_keys=True) + "\n"
        )
        metadata_path = Path(metadata_file.name)

    try:
        client.upload(metadata_path, f"{remote_block}.json")
    finally:
        metadata_path.unlink(missing_ok=True)


def archive_block(
    config: Config,
    client: Uploader,
    block_file: Path,
) -> None:
    if already_archived(config, block_file):
        LOG.debug("Already archived: %s", block_file.name)
        return

    LOG.info("Archiving %s", block_file.name)

    before = _source_signature(
        block_file,
        "before it could be archived",
    )
    metadata = _archive_metadata(config, block_file, before)
    remote_block = config.remote_url(block_file.name)
    marker = ArchiveMarker(
        file=block_file.name,
        size=metadata.size,
        sha256=metadata.checksum,
        destination=remote_block,
        endpoint=config.s3_endpoint.rstrip("/"),
        source_signature=before,
        height_ranges=metadata.height_ranges,
    )

    client.upload(block_file, remote_block)
    _ensure_source_unchanged(block_file, before, "during block upload")
    _upload_metadata_sidecar(config, client, marker, remote_block)
    _ensure_source_unchanged(block_file, before, "during upload")
    client.verify(remote_block, marker.sidecar_json())
    _ensure_source_unchanged(block_file, before, "during verification")

    write_marker(
        config,
        block_file,
        metadata.checksum,
        metadata.size,
        height_ranges=metadata.height_ranges,
        source_signature=before,
    )

    LOG.info(
        "Archived %s (%d bytes, sha256=%s)",
        block_file.name,
        metadata.size,
        metadata.checksum,
    )


def archive(config: Config, client: Uploader | None = None) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)

    uploader = client if client is not None else S5cmdClient(config)

    with exclusive_lock(config.lock_path) as acquired:
        if not acquired:
            LOG.info("Another archive process is already running")
            return

        validate_block_directory(config.block_dir)
        if config.prune_after_archive:
            require_manual_pruning(config)

        blocks = find_archivable_blocks(config)

        if not blocks:
            LOG.info("No completed block files to archive")
        else:
            LOG.info("Found %d block file(s) to archive", len(blocks))

            for block_file in blocks:
                archive_block(config, uploader, block_file)

        # Only reached when every selected block file is safely in S3.
        if config.prune_after_archive:
            prune_archived_blocks(config, uploader)
