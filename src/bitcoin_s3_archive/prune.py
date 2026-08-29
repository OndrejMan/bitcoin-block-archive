"""Explicit, archive-driven pruning of the local block store.

With `prune=1` in bitcoin.conf Bitcoin Core never prunes on its own; blocks
disappear only when `pruneblockchain <height>` is called. Driving that call
from here turns the archive/prune race into a handshake: nothing is deleted
until it is known to be in S3.
"""

from __future__ import annotations

from pathlib import Path

from bitcoin_s3_archive.bitcoin import (
    block_height,
    chain_height,
    prune_blockchain,
)
from bitcoin_s3_archive.blockfile import first_block_hash
from bitcoin_s3_archive.config import Config
from bitcoin_s3_archive.logging_setup import LOG
from bitcoin_s3_archive.state import already_archived


def unarchived_blocks(config: Config) -> list[Path]:
    """Block files present on disk that carry no completed marker."""
    return [
        path
        for path in sorted(
            config.block_dir.glob("blk*.dat"),
            key=lambda path: path.name,
        )
        if not already_archived(config, path)
    ]


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


def prune_archived_blocks(config: Config) -> None:
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
