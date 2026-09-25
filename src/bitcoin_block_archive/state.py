"""Per-block markers recording what has already been archived."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.jsonutil import load_object, require_int, require_object
from bitcoin_block_archive.models import ArchiveMarker, BlockReference, FileSignature

BLOCK_NAME = re.compile(r"^blk([0-9]{5})\.dat$")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def file_signature(path: Path) -> FileSignature:
    stat = path.stat()
    return FileSignature(stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _block_reference(value: object, context: str) -> BlockReference | None:
    if value is None:
        return None
    block = require_object(value, context)
    digest = block.get("hash")
    height = require_int(block.get("height"), f"{context} height")
    if (
        not isinstance(digest, str)
        or SHA256_HEX.fullmatch(digest) is None
        or height < 0
    ):
        raise ArchiveError(f"{context} has invalid hash or height")
    return BlockReference(digest, height)


def _read_marker_payload(path: Path, context: str) -> dict[str, object]:
    try:
        return load_object(path.read_text(encoding="utf-8"), context)
    except (OSError, UnicodeError) as error:
        raise ArchiveError(f"Cannot read archive marker {path}: {error}") from error


def _marker_identity(
    payload: dict[str, object], path: Path, context: str
) -> tuple[str, int, str]:
    name, checksum = payload.get("file"), payload.get("sha256")
    size = require_int(payload.get("size"), f"{context} size")
    if (
        not isinstance(name, str)
        or BLOCK_NAME.fullmatch(name) is None
        or path.name != f"{name}.json"
        or size < 0
        or not isinstance(checksum, str)
        or SHA256_HEX.fullmatch(checksum) is None
    ):
        raise ArchiveError(f"{context} has invalid file, size or SHA-256")
    return name, size, checksum


def _marker_target(
    config: Config, payload: dict[str, object], name: str, context: str
) -> tuple[str, str]:
    destination = config.remote_url(name)
    endpoint = config.s3_endpoint.rstrip("/")
    if payload.get("destination") != destination:
        raise ArchiveError(f"{context} belongs to another destination")
    if payload.get("endpoint") != endpoint:
        raise ArchiveError(
            f"{context} has a missing or different endpoint"
        )
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
    name, size, checksum = _marker_identity(payload, path, context)
    destination, endpoint = _marker_target(config, payload, name, context)
    signature = _source_signature(payload, size, context)
    return ArchiveMarker(
        file=name,
        size=size,
        sha256=checksum,
        destination=destination,
        endpoint=endpoint,
        source_signature=signature,
        first_block=_block_reference(
            payload.get("first_block"), f"{context} first_block"
        ),
        last_block=_block_reference(payload.get("last_block"), f"{context} last_block"),
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


def iter_markers(config: Config) -> Iterator[Path]:
    """Yield durable per-block markers in a stable order."""
    if not config.state_dir.is_dir():
        return iter(())
    return iter(sorted(config.state_dir.glob("blk*.dat.json")))


def write_marker(
    config: Config,
    block_file: Path,
    checksum: str,
    size: int,
    *,
    first_block: BlockReference | None = None,
    last_block: BlockReference | None = None,
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
        first_block=first_block,
        last_block=last_block,
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
