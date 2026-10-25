"""Explicit, archive-driven pruning of the local block store.

With `prune=1` in bitcoin.conf Bitcoin Core never prunes on its own; blocks
disappear only when `pruneblockchain <height>` is called. Driving that call
from here turns the archive/prune race into a handshake: nothing is deleted
until it is known to be in S3.
"""

from __future__ import annotations

from pathlib import Path

from bitcoin_block_archive.bitcoin import (
    block_height,
    chain_height,
    prune_blockchain,
    require_safe_pruning,
)
from bitcoin_block_archive.blockfile import first_block_hash
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.logging_setup import LOG
from bitcoin_block_archive.s3 import S5cmdClient, Uploader
from bitcoin_block_archive.state import (
    marker_matches_file,
    marker_path,
    read_marker,
    unarchived_blocks,
)


def safe_prune_height(
    config: Config, *, height_cache: dict[str, int] | None = None
) -> int | None:
    """Highest height that cannot delete an unarchived block file.

    Bitcoin Core deletes a blk/rev pair only once the *highest* block it
    contains is at or below the requested height. Staying one below the
    *lowest* height in every unarchived file is therefore safe even though
    blocks are written in arrival order rather than by height: the first
    block of a file is a lower bound on that file's highest block.
    """
    on_disk = sorted(config.block_dir.glob("blk*.dat"))

    if not on_disk:
        LOG.debug("No block files on disk; nothing to prune")
        return None

    pending = unarchived_blocks(config)

    if not pending:
        # Everything on disk is in S3; let Bitcoin Core clamp to the
        # blocks it insists on keeping around the tip.
        return chain_height(config)

    heights = []
    cache = height_cache if height_cache is not None else {}

    for block_file in pending:
        block = first_block_hash(block_file)

        if block is None:
            LOG.debug("%s holds no block yet", block_file.name)
            continue

        if block not in cache:
            cache[block] = block_height(config, block)
        heights.append(cache[block])

    if not heights:
        return None

    # Downloaded blocks may be ahead of the active chain during synchronization.
    # Core rejects a manual prune height above its active tip.
    return min(min(heights) - 1, chain_height(config))


def read_prune_ceiling(path: Path) -> int | None:
    """Height in a consumer's progress file, or None when it cannot be read."""
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as error:
        LOG.warning("Cannot read the prune ceiling from %s: %s", path, error)
        return None


def _verify_archived_local_files(config: Config, uploader: Uploader) -> None:
    """Verify remote copies of locally present files with valid markers."""
    for path in sorted(config.block_dir.glob("blk*.dat")):
        marker_file = marker_path(config, path)
        if not marker_file.is_file():
            continue
        marker = read_marker(config, marker_file)
        if marker_matches_file(marker, path):
            uploader.verify(
                config.remote_url(path.name),
                marker.sidecar_json(),
            )


def prune_archived_blocks(
    config: Config,
    client: Uploader | None = None,
    *,
    height_cache: dict[str, int] | None = None,
    strict: bool = False,
) -> None:
    require_safe_pruning(config, archiver_prunes=True)
    uploader = client if client is not None else S5cmdClient(config)
    _verify_archived_local_files(config, uploader)
    height = safe_prune_height(config, height_cache=height_cache)

    boundary = config.prune_retain_from_file
    if height is not None and boundary is not None:
        resume_file = config.block_dir / f"blk{boundary:05d}.dat"
        if not resume_file.is_file():
            if strict:
                raise ArchiveError(f"Parser resume file {resume_file} is missing")
            LOG.warning("Parser resume file %s is missing; not pruning", resume_file)
            return
        # Core prunes by the highest height in each file, not by file number.
        # A height below one known block in EVERY protected file preserves all
        # those files even when arrival order differs from chain height.
        for path in sorted(config.block_dir.glob("blk*.dat")):
            if int(path.stem[3:]) < boundary:
                continue
            block = first_block_hash(path)
            if block is None:
                if strict:
                    raise ArchiveError(f"Cannot bound protected parser file {path}")
                LOG.warning("Cannot bound protected file %s; not pruning", path)
                return
            height = min(height, block_height(config, block) - 1)

    if height is not None and config.prune_ceiling_file is not None:
        ceiling = read_prune_ceiling(config.prune_ceiling_file)

        if ceiling is None:
            if strict:
                raise ArchiveError("Cannot read the consumer pruning ceiling")
            LOG.info("No prune ceiling; not pruning")
            return

        height = min(height, ceiling)

    if height is None:
        if strict:
            raise ArchiveError("Cannot determine a safe pruning height")
        LOG.info("No safe prune height could be determined; not pruning")
        return

    if height <= 0:
        LOG.info("Nothing archived far enough back to prune yet")
        return

    LOG.info("Pruning blocks up to height %d", height)

    pruned = prune_blockchain(config, height)

    LOG.info("Bitcoin Core pruned up to height %d", pruned)
