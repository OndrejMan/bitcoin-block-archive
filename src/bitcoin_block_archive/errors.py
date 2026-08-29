"""Exceptions raised by the archiver."""

from __future__ import annotations


class ArchiveError(RuntimeError):
    """A block file could not be archived safely."""
