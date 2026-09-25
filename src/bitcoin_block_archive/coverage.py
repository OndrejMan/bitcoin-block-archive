"""Conservative blockchain coverage advertised by the archive manifest."""

from __future__ import annotations

from bitcoin_block_archive.bitcoin import block_height, chain_height
from bitcoin_block_archive.blockfile import block_hash, block_headers
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.state import (
    BLOCK_NAME,
    already_archived,
    file_signature,
    iter_markers,
    read_marker,
)


def archived_height(config: Config) -> int | None:
    """Conservative coverage, accounting for every block in pending files.

    File pruning only needs one known height per pending file. Coverage needs
    the lowest height of *all* pending blocks, bounded by the validated tip
    before reading the files. Adjacent linked headers need only one RPC.
    """
    markers = [read_marker(config, path) for path in iter_markers(config)]
    if not markers:
        return None
    files = sorted(config.block_dir.glob("blk*.dat"))
    names = sorted({path.name for path in files} | {m.file for m in markers})
    for number, name in enumerate(names):
        match = BLOCK_NAME.fullmatch(name)
        if match is None or int(match.group(1)) != number:
            return None
    tip = chain_height(config)
    signatures = {path: file_signature(path) for path in files}
    pending = [path for path in files if not already_archived(config, path)]
    heights: dict[str, int] = {}
    coverage = tip
    for path in pending:
        for header in block_headers(path):
            digest = block_hash(header)
            previous = header[4:36][::-1].hex()
            if previous in heights:
                height = heights[previous] + 1
            else:
                height = block_height(config, digest)
            heights[digest] = height
            coverage = min(coverage, height - 1)
    if not heights:
        # Never advertise a newer live tip solely because all markers exist.
        recorded = [
            block.height
            for marker in markers
            for block in (marker.first_block, marker.last_block)
            if block is not None
        ]
        if not recorded:
            return None
        coverage = min(coverage, max(recorded))
    if sorted(config.block_dir.glob("blk*.dat")) != files or any(
        file_signature(path) != signature for path, signature in signatures.items()
    ):
        raise ArchiveError("Block files changed while checking archive coverage")
    return coverage if coverage >= 0 else None
