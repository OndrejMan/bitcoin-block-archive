"""Explicit, archive-driven pruning of the local block store.

With `prune=1` in bitcoin.conf Bitcoin Core never prunes on its own; blocks
disappear only when `pruneblockchain <height>` is called. Driving that call
from here turns the archive/prune race into a handshake: nothing is deleted
until it is known to be in S3.
"""

from __future__ import annotations

from bitcoin_block_archive.bitcoin import (
    block_height,
    chain_height,
    prune_blockchain,
    require_manual_pruning,
)
from bitcoin_block_archive.blockfile import first_block_hash
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.logging_setup import LOG
from bitcoin_block_archive.s3 import S5cmdClient, Uploader
from bitcoin_block_archive.state import (
    marker_matches_file,
    marker_path,
    read_marker,
    unarchived_blocks,
)


def safe_prune_height(config: Config) -> int | None:
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

    for block_file in pending:
        block = first_block_hash(block_file)

        if block is None:
            LOG.debug("%s holds no block yet", block_file.name)
            continue

        heights.append(block_height(config, block))

    if not heights:
        return None

    return min(heights) - 1


def prune_archived_blocks(config: Config, client: Uploader | None = None) -> None:
    require_manual_pruning(config)
    uploader = client if client is not None else S5cmdClient(config)
    # Recheck remote copies of files still at risk of being deleted locally.
    for path in sorted(config.block_dir.glob("blk*.dat")):
        marker_file = marker_path(config, path)
        if not marker_file.is_file():
            continue
        marker = read_marker(config, marker_file)
        if marker_matches_file(marker, path):
            uploader.verify(
                config.remote_url(path.name),
                marker.size,
                marker.sha256,
            )
    height = safe_prune_height(config)

    if height is None:
        LOG.info("No safe prune height could be determined; not pruning")
        return

    if height <= 0:
        LOG.info("Nothing archived far enough back to prune yet")
        return

    LOG.info("Pruning blocks up to height %d", height)

    pruned = prune_blockchain(config, height)

    LOG.info("Bitcoin Core pruned up to height %d", pruned)
