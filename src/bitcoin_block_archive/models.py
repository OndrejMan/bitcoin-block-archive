"""Typed archive records and their portable JSON representation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, TypedDict


class FileSignature(NamedTuple):
    size: int
    mtime_ns: int
    ctime_ns: int


class BlockReferenceJSON(TypedDict):
    hash: str
    height: int


@dataclass(frozen=True)
class BlockReference:
    hash: str
    height: int

    def to_json(self) -> BlockReferenceJSON:
        return {"hash": self.hash, "height": self.height}


class _ArchiveEntryRequired(TypedDict):
    file: str
    size: int
    sha256: str


class ArchiveEntryJSON(_ArchiveEntryRequired, total=False):
    first_block: BlockReferenceJSON
    last_block: BlockReferenceJSON


class _ArchiveMarkerRequired(ArchiveEntryJSON):
    destination: str
    endpoint: str
    mtime_ns: int
    ctime_ns: int


class ArchiveMarkerJSON(_ArchiveMarkerRequired):
    pass


@dataclass(frozen=True)
class ArchiveMarker:
    file: str
    size: int
    sha256: str
    destination: str
    endpoint: str
    source_signature: FileSignature
    first_block: BlockReference | None = None
    last_block: BlockReference | None = None

    def entry_json(self) -> ArchiveEntryJSON:
        entry: ArchiveEntryJSON = {
            "file": self.file,
            "size": self.size,
            "sha256": self.sha256,
        }
        if self.first_block is not None:
            entry["first_block"] = self.first_block.to_json()
        if self.last_block is not None:
            entry["last_block"] = self.last_block.to_json()
        return entry

    def to_json(self) -> ArchiveMarkerJSON:
        payload: ArchiveMarkerJSON = {
            "file": self.file,
            "size": self.size,
            "sha256": self.sha256,
            "destination": self.destination,
            "endpoint": self.endpoint,
            "mtime_ns": self.source_signature.mtime_ns,
            "ctime_ns": self.source_signature.ctime_ns,
        }
        if self.first_block is not None:
            payload["first_block"] = self.first_block.to_json()
        if self.last_block is not None:
            payload["last_block"] = self.last_block.to_json()
        return payload


class ArchiveManifestJSON(TypedDict):
    schema_version: int
    destination: str
    contiguous_from_zero: bool
    archived_max_height: int | None
    block_files: list[ArchiveEntryJSON]
