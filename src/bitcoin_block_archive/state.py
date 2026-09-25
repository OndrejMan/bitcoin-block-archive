"""Per-block markers recording what has already been archived."""

from __future__ import annotations

import json
import re
from pathlib import Path

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.jsonutil import load_object, require_int, require_list
from bitcoin_block_archive.models import (
    ARCHIVE_SIDECAR_SCHEMA_VERSION,
    ArchiveMarker,
    FileSignature,
    HeightRange,
)

BLOCK_NAME = re.compile(r"^blk([0-9]{5})\.dat$")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def file_signature(path: Path) -> FileSignature:
    stat = path.stat()
    return FileSignature(stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _height_ranges(value: object, context: str) -> tuple[HeightRange, ...]:
    items = require_list(value, context)
    if not items:
        raise ArchiveError(f"{context} must be a non-empty list")

    ranges: list[HeightRange] = []
    previous_end: int | None = None
    for index, item in enumerate(items):
        item_context = f"{context}[{index}]"
        values = require_list(item, item_context)
        if len(values) != 2:
            raise ArchiveError(f"{item_context} must contain two heights")
        start = require_int(values[0], f"{item_context} start")
        end = require_int(values[1], f"{item_context} end")
        if (
            start < 0
            or end < start
            or (previous_end is not None and start <= previous_end + 1)
        ):
            raise ArchiveError(f"{context} is not canonical")
        ranges.append((start, end))
        previous_end = end
    return tuple(ranges)


def _read_marker_payload(path: Path, context: str) -> dict[str, object]:
    try:
        return load_object(path.read_text(encoding="utf-8"), context)
    except (OSError, UnicodeError) as error:
        raise ArchiveError(f"Cannot read archive marker {path}: {error}") from error


def _marker_identity(
    payload: dict[str, object], path: Path, context: str
) -> tuple[str, int, str, tuple[HeightRange, ...]]:
    name, checksum = payload.get("file"), payload.get("sha256")
    size = require_int(payload.get("size"), f"{context} size")
    schema_version = require_int(
        payload.get("schema_version"), f"{context} schema_version"
    )
    if (
        schema_version != ARCHIVE_SIDECAR_SCHEMA_VERSION
        or not isinstance(name, str)
        or BLOCK_NAME.fullmatch(name) is None
        or path.name != f"{name}.json"
        or size < 0
        or not isinstance(checksum, str)
        or SHA256_HEX.fullmatch(checksum) is None
    ):
        raise ArchiveError(f"{context} has invalid file, size or SHA-256")
    ranges = _height_ranges(payload.get("height_ranges"), f"{context} height_ranges")
    return name, size, checksum, ranges


def _marker_target(
    config: Config, payload: dict[str, object], name: str, context: str
) -> tuple[str, str]:
    destination = config.remote_url(name)
    endpoint = config.s3_endpoint.rstrip("/")
    if payload.get("destination") != destination:
        raise ArchiveError(f"{context} belongs to another destination")
    if payload.get("endpoint") != endpoint:
        raise ArchiveError(f"{context} has a missing or different endpoint")
    return destination, endpoint


def _source_signature(
    payload: dict[str, object], size: int, context: str
) -> FileSignature:
    return FileSignature(
        size,
        require_int(payload.get("mtime_ns"), f"{context} mtime_ns"),
        require_int(payload.get("ctime_ns"), f"{context} ctime_ns"),
    )


def read_marker(config: Config, path: Path) -> ArchiveMarker:
    context = f"Archive marker {path}"
    payload = _read_marker_payload(path, context)
    name, size, checksum, height_ranges = _marker_identity(payload, path, context)
    destination, endpoint = _marker_target(config, payload, name, context)
    signature = _source_signature(payload, size, context)
    return ArchiveMarker(
        file=name,
        size=size,
        sha256=checksum,
        destination=destination,
        endpoint=endpoint,
        source_signature=signature,
        height_ranges=height_ranges,
    )


def marker_path(config: Config, block_file: Path) -> Path:
    return config.state_dir / f"{block_file.name}.json"


def already_archived(config: Config, block_file: Path) -> bool:
    path = marker_path(config, block_file)
    if not path.is_file():
        return False
    marker = read_marker(config, path)
    return marker_matches_file(marker, block_file)


def marker_matches_file(marker: ArchiveMarker, block_file: Path) -> bool:
    """Recheck the local source without loading an already validated marker again."""
    if not block_file.exists():
        return True
    signature = file_signature(block_file)
    if marker.size != signature.size:
        return False
    return marker.source_signature == signature


def write_marker(
    config: Config,
    block_file: Path,
    checksum: str,
    size: int,
    *,
    height_ranges: tuple[HeightRange, ...],
    source_signature: FileSignature,
) -> None:
    marker = marker_path(config, block_file)

    data = ArchiveMarker(
        file=block_file.name,
        size=size,
        sha256=checksum,
        destination=config.remote_url(block_file.name),
        endpoint=config.s3_endpoint.rstrip("/"),
        source_signature=source_signature,
        height_ranges=height_ranges,
    )

    temporary = marker.with_name(f"{marker.name}.tmp")

    temporary.write_text(
        json.dumps(data.to_json(), indent=2) + "\n",
        encoding="utf-8",
    )

    temporary.replace(marker)


def unarchived_blocks(config: Config) -> list[Path]:
    """Block files present on disk that carry no completed marker."""
    return [
        path
        for path in sorted(config.block_dir.glob("blk*.dat"))
        if not already_archived(config, path)
    ]
