"""Selection and archival of completed block files."""

from __future__ import annotations

import tempfile
from pathlib import Path

from bitcoin_s3_archive.config import Config
from bitcoin_s3_archive.errors import ArchiveError
from bitcoin_s3_archive.hashing import checksum_line, sha256_file
from bitcoin_s3_archive.locking import exclusive_lock
from bitcoin_s3_archive.logging_setup import LOG
from bitcoin_s3_archive.prune import prune_archived_blocks
from bitcoin_s3_archive.s3 import S5cmdClient, Uploader
from bitcoin_s3_archive.state import already_archived, write_marker

BLOCK_FILE_GLOB = "blk*.dat"


def find_archivable_blocks(config: Config) -> list[Path]:
    """Completed block files, newest `keep_latest_files` held back."""
    block_files = sorted(
        config.block_dir.glob(BLOCK_FILE_GLOB),
        key=lambda path: path.name,
    )

    if len(block_files) <= config.keep_latest_files:
        return []

    if config.keep_latest_files <= 0:
        return block_files

    return block_files[: -config.keep_latest_files]


def _stat_size_and_mtime(block_file: Path, when: str) -> tuple[int, int]:
    try:
        stat = block_file.stat()
    except FileNotFoundError as error:
        raise ArchiveError(
            f"{block_file} disappeared {when}"
        ) from error

    return stat.st_size, stat.st_mtime_ns


def _upload_checksum(
    config: Config,
    client: Uploader,
    block_file: Path,
    checksum: str,
    remote_block: str,
) -> None:
    """Store the SHA256 beside the blk*.dat object."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix=f"{block_file.name}.",
        suffix=".sha256",
        dir=config.state_dir,
        delete=False,
    ) as checksum_file:
        checksum_file.write(checksum_line(checksum, block_file.name))
        checksum_path = Path(checksum_file.name)

    try:
        client.upload(checksum_path, f"{remote_block}.sha256")
    finally:
        checksum_path.unlink(missing_ok=True)


def archive_block(
    config: Config,
    client: Uploader,
    block_file: Path,
) -> None:
    if already_archived(config, block_file):
        LOG.debug("Already archived: %s", block_file.name)
        return

    LOG.info("Archiving %s", block_file.name)

    before = _stat_size_and_mtime(
        block_file,
        "before it could be archived",
    )

    checksum = sha256_file(block_file)

    after = _stat_size_and_mtime(
        block_file,
        "while calculating checksum",
    )

    # blk*.dat selected for archival should already be closed/immutable.
    # If it changed while hashing, do not upload it.
    if before != after:
        raise ArchiveError(f"{block_file} changed while being archived")

    size = after[0]
    remote_block = config.remote_url(block_file.name)

    client.upload(block_file, remote_block)

    _upload_checksum(
        config,
        client,
        block_file,
        checksum,
        remote_block,
    )

    write_marker(
        config,
        block_file,
        checksum,
        size,
    )

    LOG.info(
        "Archived %s (%d bytes, sha256=%s)",
        block_file.name,
        size,
        checksum,
    )


def archive(config: Config, client: Uploader | None = None) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)

    uploader = client if client is not None else S5cmdClient(config)

    with exclusive_lock(config.lock_path) as acquired:
        if not acquired:
            LOG.info("Another archive process is already running")
            return

        blocks = find_archivable_blocks(config)

        if not blocks:
            LOG.info("No completed block files to archive")
        else:
            LOG.info("Found %d block file(s) to archive", len(blocks))

            for block_file in blocks:
                archive_block(config, uploader, block_file)

        # Only reached when every selected block file is safely in S3.
        if config.prune_after_archive:
            prune_archived_blocks(config)
