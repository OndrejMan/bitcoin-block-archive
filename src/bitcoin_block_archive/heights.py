"""Heights of the blocks stored in one blk*.dat file.

Headers carry no height, only the previous block's hash. Blocks are linked
through those hashes, so the node is asked once per connected group (for its
oldest block in the file) and every descendant is that height plus its depth.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from bitcoin_block_archive.blockfile import block_hash
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.models import HeightRange

HeightLookup = Callable[[str], int]


def parent_links(headers: Iterable[bytes]) -> dict[str, str]:
    """Map each block hash to its parent's hash, both as Bitcoin Core prints them.

    Header bytes 4..36 hold the previous block hash in little-endian order.
    """
    return {block_hash(header): header[4:36][::-1].hex() for header in headers}


def _unresolved_ancestry(
    digest: str,
    parents: dict[str, str],
    heights: dict[str, int],
    context: str,
) -> tuple[list[str], str]:
    """Walk up from `digest` until a resolved block or one outside the file.

    Returns the walked blocks, child first, and the hash the walk stopped at.
    """
    trail: list[str] = []
    visiting: set[str] = set()
    current = digest
    while current in parents and current not in heights:
        if current in visiting:
            raise ArchiveError(f"{context} contains cyclic block ancestry")
        visiting.add(current)
        trail.append(current)
        current = parents[current]
    return trail, current


def _assign_heights(
    trail: list[str],
    stop: str,
    heights: dict[str, int],
    lookup: HeightLookup,
) -> None:
    """Give every block in `trail` its height, asking the node only for a root."""
    height: int
    descendants: list[str]
    if stop in heights:
        height = heights[stop]
        descendants = trail
    else:
        *descendants, root = trail
        height = lookup(root)
        heights[root] = height

    for child in reversed(descendants):
        height += 1
        heights[child] = height


def resolve_heights(
    parents: dict[str, str], lookup: HeightLookup, context: str
) -> dict[str, int]:
    heights: dict[str, int] = {}
    for digest in parents:
        if digest not in heights:
            trail, stop = _unresolved_ancestry(digest, parents, heights, context)
            _assign_heights(trail, stop, heights, lookup)
    return heights


def compact_ranges(heights: Iterable[int]) -> tuple[HeightRange, ...]:
    """Collapse heights into sorted, non-adjacent, inclusive intervals."""
    ranges: list[HeightRange] = []
    for height in sorted(set(heights)):
        if ranges and height == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], height)
        else:
            ranges.append((height, height))
    return tuple(ranges)


def block_height_ranges(
    headers: Iterable[bytes], lookup: HeightLookup, context: str
) -> tuple[HeightRange, ...]:
    parents = parent_links(headers)
    if not parents:
        raise ArchiveError(f"{context} holds no complete Bitcoin block")
    return compact_ranges(resolve_heights(parents, lookup, context).values())
