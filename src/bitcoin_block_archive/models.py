"""Typed archive records and their portable JSON representation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, TypedDict

ARCHIVE_SIDECAR_SCHEMA_VERSION = 1
HeightRange = tuple[int, int]


class FileSignature(NamedTuple):
    size: int
    mtime_ns: int
    ctime_ns: int


class ArchiveSidecarJSON(TypedDict):
    schema_version: int
    file: str
    size: int
    sha256: str
    height_ranges: list[list[int]]


class _ArchiveMarkerRequired(ArchiveSidecarJSON):
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
    height_ranges: tuple[HeightRange, ...]

    def sidecar_json(self) -> ArchiveSidecarJSON:
        return {
            "schema_version": ARCHIVE_SIDECAR_SCHEMA_VERSION,
            "file": self.file,
            "size": self.size,
            "sha256": self.sha256,
            "height_ranges": [[start, end] for start, end in self.height_ranges],
        }

    def to_json(self) -> ArchiveMarkerJSON:
        payload: ArchiveMarkerJSON = {
            **self.sidecar_json(),
            "destination": self.destination,
            "endpoint": self.endpoint,
            "mtime_ns": self.source_signature.mtime_ns,
            "ctime_ns": self.source_signature.ctime_ns,
        }
        return payload
