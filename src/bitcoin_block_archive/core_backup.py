"""Small, offline Core snapshots; Compose lifecycle belongs to core-backup.sh."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import shutil
import stat
import tarfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from bitcoin_block_archive.bitcoin import cli
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.disk import format_size, parse_size
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.hashing import sha256_file
from bitcoin_block_archive.jsonutil import load_object, require_int
from bitcoin_block_archive.logging_setup import LOG
from bitcoin_block_archive.process import run
from bitcoin_block_archive.prune import read_prune_ceiling, safe_prune_height
from bitcoin_block_archive.s3 import S5cmdClient
from bitcoin_block_archive.state import marker_matches_file, marker_path, read_marker

ROOTS = ("blocks", "chainstate")


@dataclass(frozen=True)
class Settings:
    work: Path
    destination: str
    max_size: int
    max_blocks: int
    max_lag: int

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls(
            work=Path("/backup"),
            destination=os.environ.get(
                "CORE_BACKUP_DESTINATION",
                "s3://xman-coinjoin-new/bitcoin-mainnet/core-snapshots",
            ).rstrip("/"),
            max_size=parse_size(os.environ.get("CORE_BACKUP_MAX_SIZE", "25G")),
            max_blocks=parse_size(os.environ.get("CORE_BACKUP_MAX_BLOCK_SIZE", "2G")),
            max_lag=int(os.environ.get("CORE_BACKUP_MAX_LAG", "2016")),
        )
        if not re.fullmatch(r"s3://[^/]+/.+", settings.destination):
            raise ArchiveError("CORE_BACKUP_DESTINATION must be a dedicated S3 prefix")
        if min(settings.max_size, settings.max_blocks, settings.max_lag) <= 0:
            raise ArchiveError("Core backup limits must be positive")
        return settings


def rpc(config: Config, method: str) -> dict[str, object]:
    result = cli(config, method, check=False)
    if result.returncode:
        raise ArchiveError(f"Cannot query Core: {method}")
    return load_object(result.stdout, method)


def require_pruned(info: dict[str, object], settings: Settings) -> None:
    if info.get("chain") != "main":
        raise ArchiveError("Core snapshot requires mainnet")
    if info.get("pruned") is not True or info.get("automatic_pruning") is not False:
        raise ArchiveError("Core snapshot requires archive-driven manual pruning")
    height = require_int(info.get("blocks"), "blocks")
    prune_height = require_int(info.get("pruneheight"), "pruneheight")
    if (
        info.get("initialblockdownload") is not False
        or require_int(info.get("headers"), "headers") != height
    ):
        raise ArchiveError("Core has not finished synchronizing")
    if not 0 < prune_height <= height or height - prune_height > settings.max_lag:
        raise ArchiveError("Pruning has not caught up; snapshot skipped")


def inventory(datadir: Path, settings: Settings) -> list[Path]:
    """Count logical file bytes, including preallocation, before copying anything."""
    paths: list[Path] = []
    total = 0
    blocks = 0
    for root in ROOTS:
        directory = datadir / root
        if directory.is_symlink() or not directory.is_dir():
            raise ArchiveError(f"Missing regular {root} directory")
        for path in sorted(directory.rglob("*")):
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ArchiveError(f"Snapshot refuses links or special files: {path}")
            size = path.stat().st_size
            total += size
            if root == "blocks" and path.suffix == ".dat":
                blocks += size
            if total > settings.max_size or blocks > settings.max_blocks:
                raise ArchiveError("Core data exceeds snapshot size limits")
            paths.append(path)
    for database in (datadir / "chainstate", datadir / "blocks/index"):
        if not (database / "CURRENT").is_file():
            raise ArchiveError(f"Missing LevelDB CURRENT: {database}")
    if not list((datadir / "blocks").glob("blk*.dat")):
        raise ArchiveError("No retained blocks")
    LOG.info(
        "Snapshot source: %s; block/undo data: %s",
        format_size(total),
        format_size(blocks),
    )
    return paths


def check(config: Config, settings: Settings) -> dict[str, object]:
    info = rpc(config, "getblockchaininfo")
    require_pruned(info, settings)
    if rpc(config, "getindexinfo"):
        raise ArchiveError("Disable optional Core indexes before taking a snapshot")
    # A second chainstate (AssumeUTXO) is outside this single-chainstate format.
    if list(config.bitcoin_datadir.glob("chainstate_*")):
        raise ArchiveError("Multiple chainstates are not supported by Core snapshots")
    inventory(config.bitcoin_datadir, settings)
    require_pruning_complete(config, info)
    return {
        "checked_at": time.time(),
        # Core may accept another block before shutdown; this is an observation,
        # not a claimed checkpoint of the subsequent offline snapshot.
        "observed_chain_info": info,
        "core_version": rpc(config, "getnetworkinfo").get("version"),
    }


def require_pruning_complete(config: Config, info: dict[str, object]) -> None:
    from bitcoin_block_archive.cli import Arguments, build_parser

    args = Arguments()
    build_parser().parse_args(
        shlex.split(os.environ.get("CORE_BACKUP_ARCHIVER_ARGS", "")), namespace=args
    )
    if not args.prune_after_archive:
        raise ArchiveError("Enable archive-driven pruning before Core snapshots")
    pruning = replace(
        config,
        keep_latest_files=args.keep_latest_files,
        prune_after_archive=True,
        prune_ceiling_file=args.prune_ceiling_file,
    )
    height = safe_prune_height(pruning)
    if height is None:
        raise ArchiveError("Cannot determine completed pruning")
    # Core 29.1 keeps at least the latest 288 blocks and its active block file.
    height = min(height, require_int(info.get("blocks"), "blocks") - 288)
    if pruning.prune_ceiling_file is not None:
        ceiling = read_prune_ceiling(pruning.prune_ceiling_file)
        if ceiling is None:
            raise ArchiveError("Cannot read Dumplings' pruning ceiling")
        height = min(height, ceiling)
    for path in sorted(pruning.block_dir.glob("blk*.dat"))[:-1]:
        marker_file = marker_path(pruning, path)
        if not marker_file.is_file():
            continue
        marker = read_marker(pruning, marker_file)
        if (
            marker_matches_file(marker, path)
            and marker.height_ranges
            and max(end for _, end in marker.height_ranges) <= height
        ):
            raise ArchiveError("Archived files still await pruning; snapshot skipped")


def write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


@contextmanager
def stopped_core(datadir: Path) -> Iterator[None]:
    # Core uses a POSIX record lock. A shared lock works on the read-only mount
    # and conflicts with Core's exclusive lock for the whole copy operation.
    with (datadir / ".lock").open("rb") as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ArchiveError("Core is still running; snapshot refused") from error
        if (datadir / ".cookie").exists():
            raise ArchiveError("Core shutdown has not removed its RPC cookie")
        yield


def create(config: Config, settings: Settings) -> None:
    receipt = load_object((settings.work / "preflight.json").read_text(), "preflight")
    checked_at = receipt.get("checked_at")
    if (
        not isinstance(checked_at, (int, float))
        or not 0 <= time.time() - checked_at < 900
    ):
        raise ArchiveError("Missing or expired Core backup preflight")
    info = receipt.get("observed_chain_info")
    if not isinstance(info, dict):
        raise ArchiveError("Missing preflight chain information")
    # Parse again through the typed JSON boundary.
    require_pruned(load_object(json.dumps(info), "preflight chain"), settings)
    with stopped_core(config.bitcoin_datadir):
        files = inventory(config.bitcoin_datadir, settings)
        total = sum(path.stat().st_size for path in files)
        if shutil.disk_usage(settings.work).free < total + 1024**3:
            raise ArchiveError("Not enough local staging space for Core snapshot")
        temporary = settings.work / "core.tar.tmp"
        try:
            with tarfile.open(temporary, "w", dereference=True) as archive:
                for path in files:
                    archive.add(
                        path,
                        arcname=str(path.relative_to(config.bitcoin_datadir)),
                        recursive=False,
                    )
            if temporary.stat().st_size > settings.max_size:
                raise ArchiveError("Core tar exceeds snapshot size limit")
            temporary.replace(settings.work / "core.tar")
        finally:
            temporary.unlink(missing_ok=True)
    archive_path = settings.work / "core.tar"
    write_json(
        settings.work / "manifest.json",
        {
            "schema_version": 1,
            "snapshot_id": uuid.uuid4().hex,
            "created_at": int(time.time()),
            "observed_chain_info": receipt["observed_chain_info"],
            "core_version": receipt["core_version"],
            "size": archive_path.stat().st_size,
            "sha256": sha256_file(archive_path),
        },
    )


class Storage:
    """Use the archiver's credential handling and bounded s5cmd processes."""

    def __init__(self, config: Config) -> None:
        self.client = S5cmdClient(config)
        self.timeout = config.upload_timeout

    def command(self, *arguments: str) -> str:
        result = run(
            [*self.client.base_command(), *arguments], check=False, timeout=self.timeout
        )
        if result.returncode:
            raise ArchiveError("Core snapshot S3 operation failed")
        return result.stdout

    def read_json(self, url: str) -> dict[str, object]:
        return load_object(self.command("cat", url), "Core snapshot metadata")

    def verify_size(self, url: str, size: int) -> None:
        info = load_object(self.command("--json", "head", url), "S3 HEAD")
        if info.get("key") != url or require_int(info.get("size"), "S3 size") != size:
            raise ArchiveError("Core snapshot remote size/key mismatch")


def validate_manifest(manifest: dict[str, object], settings: Settings) -> str:
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
    ):
        raise ArchiveError("Unsupported Core snapshot schema")
    identifier = manifest.get("snapshot_id")
    checksum = manifest.get("sha256")
    if not isinstance(identifier, str) or not re.fullmatch("[0-9a-f]{32}", identifier):
        raise ArchiveError("Invalid Core snapshot ID")
    if not isinstance(checksum, str) or not re.fullmatch("[0-9a-f]{64}", checksum):
        raise ArchiveError("Invalid Core snapshot checksum")
    if not 0 < require_int(manifest.get("size"), "snapshot size") <= settings.max_size:
        raise ArchiveError("Core snapshot exceeds size limit")
    return identifier


def upload(config: Config, settings: Settings, storage: Storage) -> None:
    manifest_path = settings.work / "manifest.json"
    manifest = load_object(manifest_path.read_text(), "snapshot manifest")
    identifier = validate_manifest(manifest, settings)
    archive = settings.work / "core.tar"
    if (
        archive.stat().st_size != manifest["size"]
        or sha256_file(archive) != manifest["sha256"]
    ):
        raise ArchiveError("Staged Core snapshot failed checksum validation")
    prefix = f"{settings.destination}/{identifier}"
    storage.client.upload(archive, f"{prefix}/core.tar")
    storage.verify_size(f"{prefix}/core.tar", archive.stat().st_size)
    storage.client.upload(manifest_path, f"{prefix}/manifest.json")
    remote = storage.read_json(f"{prefix}/manifest.json")
    if json.dumps(remote, sort_keys=True) != json.dumps(manifest, sort_keys=True):
        raise ArchiveError("Core snapshot remote manifest mismatch")
    # Publish the pointer only after both objects have been verified. Previous
    # generations remain available; a failed upload cannot replace latest.json.
    storage.client.upload(manifest_path, f"{settings.destination}/latest.json")
    LOG.info("Published Core snapshot %s", prefix)


def unpack(archive_path: Path, destination: Path, settings: Settings) -> None:
    """Only materialize regular files under the two database roots."""
    total = 0
    blocks = 0
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or len(path.parts) < 2
                or path.parts[0] not in ROOTS
                or member.name in seen
                or member.size < 0
            ):
                raise ArchiveError("Unsafe or duplicate Core snapshot entry")
            seen.add(member.name)
            total += member.size
            if path.parts[0] == "blocks" and path.suffix == ".dat":
                blocks += member.size
            if total > settings.max_size or blocks > settings.max_blocks:
                raise ArchiveError("Extracted Core snapshot exceeds size limits")
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ArchiveError("Missing Core snapshot member data")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o600)
    inventory(destination, settings)


def restore(config: Config, settings: Settings, storage: Storage) -> None:
    datadir = config.bitcoin_datadir
    if datadir.is_symlink() or any(p.name != ".lock" for p in datadir.iterdir()):
        raise ArchiveError("Restore requires an empty Core datadir")
    lock_path = datadir / ".lock"
    if lock_path.is_symlink():
        raise ArchiveError("Restore refuses a symlinked Core lock")
    with lock_path.open("a+b") as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ArchiveError("Core is running; restore refused") from error
        if any(p.name != ".lock" for p in datadir.iterdir()):
            raise ArchiveError("Core datadir is no longer empty")
        _restore(config, settings, storage)


def _restore(config: Config, settings: Settings, storage: Storage) -> None:
    datadir = config.bitcoin_datadir
    manifest = storage.read_json(f"{settings.destination}/latest.json")
    identifier = validate_manifest(manifest, settings)
    archive = settings.work / "restore.tar"
    url = f"{settings.destination}/{identifier}/core.tar"
    storage.verify_size(url, require_int(manifest["size"], "snapshot size"))
    if (
        shutil.disk_usage(settings.work).free
        < require_int(manifest["size"], "snapshot size") + 1024**3
    ):
        raise ArchiveError("Not enough staging space to download Core snapshot")
    storage.command("cp", url, str(archive))
    if (
        archive.stat().st_size != manifest["size"]
        or sha256_file(archive) != manifest["sha256"]
    ):
        raise ArchiveError("Downloaded Core snapshot failed checksum validation")
    if shutil.disk_usage(datadir).free < settings.max_size + 1024**3:
        raise ArchiveError("Not enough destination space to restore Core snapshot")
    staging = datadir / f".restore-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        unpack(archive, staging, settings)
        for root in ROOTS:
            (staging / root).rename(datadir / root)
    finally:
        shutil.rmtree(staging)
    LOG.info("Core restored; start it with manual pruning and no optional indexes")


def main(config: Config, action: str) -> int:
    try:
        settings = Settings.from_env()
        if (
            os.environ.get("CORE_BACKUP_PRUNE") != "1"
            or os.environ.get("CORE_BACKUP_TXINDEX") != "0"
        ):
            raise ArchiveError(
                "Set BITCOIN_PRUNE=1 and BITCOIN_TXINDEX=0 for Core snapshots"
            )
        settings.work.mkdir(parents=True, exist_ok=True)
        if action in {"check", "prepare"}:
            receipt = check(config, settings)
            if action == "prepare":
                write_json(settings.work / "preflight.json", receipt)
        elif action == "create":
            create(config, settings)
        elif action == "upload":
            upload(config, settings, Storage(config))
        elif action == "restore":
            restore(config, settings, Storage(config))
        else:
            raise ArchiveError("Unknown Core backup action")
    except (ArchiveError, OSError, ValueError, tarfile.TarError) as error:
        LOG.error("Core snapshot %s refused or failed: %s", action, error)
        return 3 if action == "check" else 1
    return 0
