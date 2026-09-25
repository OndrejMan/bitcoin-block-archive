from __future__ import annotations

import pytest

from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.heights import (
    block_height_ranges,
    compact_ranges,
    resolve_heights,
)


def test_compact_ranges_merges_adjacent_and_drops_duplicates() -> None:
    assert compact_ranges([5, 3, 4, 4, 9, 11, 10]) == ((3, 5), (9, 11))


def test_compact_ranges_of_nothing_is_empty() -> None:
    assert compact_ranges([]) == ()


def test_resolve_heights_asks_once_per_connected_group() -> None:
    parents = {"c": "b", "b": "a", "a": "outside", "e": "d", "d": "elsewhere"}
    roots = {"a": 100, "d": 200}
    calls: list[str] = []

    def lookup(digest: str) -> int:
        calls.append(digest)
        return roots[digest]

    heights = resolve_heights(parents, lookup, "test")

    assert heights == {"a": 100, "b": 101, "c": 102, "d": 200, "e": 201}
    assert sorted(calls) == ["a", "d"]


def test_resolve_heights_rejects_cyclic_ancestry() -> None:
    with pytest.raises(ArchiveError, match="cyclic block ancestry"):
        resolve_heights({"a": "b", "b": "a"}, lambda _: 0, "test")


def test_block_height_ranges_rejects_empty_file() -> None:
    with pytest.raises(ArchiveError, match="holds no complete Bitcoin block"):
        block_height_ranges([], lambda _: 0, "blk00000.dat")
