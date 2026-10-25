"""Command line interface."""

from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path

from bitcoin_block_archive import __version__
from bitcoin_block_archive.archive import archive
from bitcoin_block_archive.bitcoin import stop_bitcoin
from bitcoin_block_archive.config import (
    DEFAULT_BITCOIN_CLI,
    DEFAULT_BITCOIN_DATADIR,
    DEFAULT_BLOCK_DIR,
    DEFAULT_KEEP_LATEST_FILES,
    DEFAULT_MIN_FREE_SPACE,
    DEFAULT_RPC_TIMEOUT,
    DEFAULT_S3_DESTINATION,
    DEFAULT_S3_ENDPOINT,
    DEFAULT_S3_PROFILE,
    DEFAULT_STATE_DIR,
    DEFAULT_UPLOAD_TIMEOUT,
    DEFAULT_VERIFY_TIMEOUT,
    Config,
    default_credentials,
)
from bitcoin_block_archive.disk import parse_size
from bitcoin_block_archive.errors import ArchiveError, UnsafePruningError
from bitcoin_block_archive.logging_setup import LOG, configure
from bitcoin_block_archive.schedule import (
    parse_duration,
    run_repeatedly,
    stop_on_signals,
)
from bitcoin_block_archive.watchdog import watch_disk


class Arguments(argparse.Namespace):
    """Attributes populated by build_parser, typed before constructing Config."""

    block_dir: Path
    state_dir: Path
    endpoint: str
    profile: str
    credentials: Path
    destination: str
    keep_latest_files: int
    bitcoin_cli: str
    bitcoin_datadir: Path
    rpc_host: str | None
    rpc_port: int | None
    prune_after_archive: bool
    prune_only: bool
    prune_retain_from_file: int | None
    prune_ceiling_file: Path | None
    min_free_space: int
    verbose: bool
    rpc_timeout: int
    upload_timeout: int
    verify_timeout: int
    interval: int
    core_backup: str | None


def _nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _env(name: str) -> str | None:
    """Environment value used as a flag default; empty counts as unset."""
    return os.environ.get(name) or None


def _size_argument(text: str) -> int:
    try:
        return parse_size(text)
    except (ArchiveError, OverflowError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _duration_argument(text: str) -> int:
    try:
        return parse_duration(text)
    except ArchiveError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bitcoin-block-archive",
        description=(
            "Archive completed Bitcoin Core blk*.dat files to S3 using s5cmd."
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    parser.add_argument(
        "--core-backup",
        choices=("check", "prepare", "create", "upload", "restore"),
        help="Pruned Core snapshot step; use core-backup.sh to coordinate Compose.",
    )

    parser.add_argument(
        "--block-dir",
        type=Path,
        default=DEFAULT_BLOCK_DIR,
    )

    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
    )

    parser.add_argument(
        "--endpoint",
        default=_env("S3_ENDPOINT_URL") or DEFAULT_S3_ENDPOINT,
        help="S3 endpoint URL (env: S3_ENDPOINT_URL).",
    )

    parser.add_argument(
        "--profile",
        default=_env("S3_PROFILE") or DEFAULT_S3_PROFILE,
        help="Profile in the credentials file (env: S3_PROFILE).",
    )

    parser.add_argument(
        "--credentials",
        type=Path,
        default=default_credentials(),
    )

    parser.add_argument(
        "--destination",
        default=_env("S3_DESTINATION") or DEFAULT_S3_DESTINATION,
        help="S3 prefix for block files (env: S3_DESTINATION).",
    )

    parser.add_argument(
        "--keep-latest-files",
        type=_nonnegative_int,
        default=DEFAULT_KEEP_LATEST_FILES,
        help=(
            "Do not archive the N newest blk*.dat files. "
            "The newest file may still be written by Bitcoin Core."
        ),
    )

    parser.add_argument(
        "--bitcoin-cli",
        default=DEFAULT_BITCOIN_CLI,
    )

    parser.add_argument(
        "--bitcoin-datadir",
        type=Path,
        default=_env("BITCOIN_DATADIR") or DEFAULT_BITCOIN_DATADIR,
        help="Datadir holding the RPC cookie (env: BITCOIN_DATADIR).",
    )

    parser.add_argument(
        "--rpc-host",
        default=_env("BITCOIN_RPC_HOST"),
        help=(
            "Bitcoin Core RPC host, for a node in another container "
            "(env: BITCOIN_RPC_HOST; default: bitcoin-cli's localhost)."
        ),
    )

    parser.add_argument(
        "--rpc-port",
        type=_positive_int,
        default=_env("BITCOIN_RPC_PORT"),
        metavar="PORT",
        help="Bitcoin Core RPC port (env: BITCOIN_RPC_PORT).",
    )

    parser.add_argument(
        "--prune-after-archive",
        action="store_true",
        help=(
            "After each verified batch, call `pruneblockchain` up to the "
            "highest height that cannot delete an unarchived block file. "
            "Requires prune=1 (manual pruning) in bitcoin.conf."
        ),
    )
    parser.add_argument(
        "--prune-only",
        action="store_true",
        help="Verify existing archives and prune; never upload.",
    )
    parser.add_argument(
        "--prune-retain-from-file",
        type=_nonnegative_int,
        help="Preserve this blk file number and all newer files.",
    )

    parser.add_argument(
        "--prune-ceiling-file",
        type=Path,
        metavar="PATH",
        help=(
            "Never prune above the height written in PATH, such as Dumplings' "
            "Scanner/LastProcessedBlockHeight.txt, so blocks stay until it has "
            "scanned them. A missing or unreadable file stops pruning."
        ),
    )

    parser.add_argument(
        "--min-free-space",
        type=_size_argument,
        default=DEFAULT_MIN_FREE_SPACE,
        metavar="SIZE",
        help=(
            "Stop Bitcoin Core when free space in the block directory falls "
            "below SIZE (default: 20G), checked every 30 seconds even during "
            "uploads. 0 disables the watchdog."
        ),
    )

    parser.add_argument(
        "--interval",
        type=_duration_argument,
        default=_env("ARCHIVE_INTERVAL") or "0",
        metavar="DURATION",
        help=(
            "Repeat the pass every DURATION (e.g. 30m) until SIGTERM or "
            "SIGINT; 0 runs a single pass (env: ARCHIVE_INTERVAL)."
        ),
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
    )

    for flag, default, description in (
        ("--rpc-timeout", DEFAULT_RPC_TIMEOUT, "Time limit for each bitcoin-cli call"),
        ("--upload-timeout", DEFAULT_UPLOAD_TIMEOUT, "Time limit for each S3 upload"),
        (
            "--verify-timeout",
            DEFAULT_VERIFY_TIMEOUT,
            "Time limit for each S3 HEAD or sidecar read",
        ),
    ):
        parser.add_argument(
            flag,
            type=_positive_int,
            default=default,
            metavar="SECONDS",
            help=f"{description} (default: {default}s).",
        )

    return parser


def config_from_args(args: Arguments) -> Config:
    return Config(
        block_dir=args.block_dir,
        state_dir=args.state_dir,
        s3_endpoint=args.endpoint,
        s3_profile=args.profile,
        s3_credentials=args.credentials,
        s3_destination=args.destination,
        keep_latest_files=args.keep_latest_files,
        prune_after_archive=args.prune_after_archive,
        prune_ceiling_file=args.prune_ceiling_file,
        prune_retain_from_file=args.prune_retain_from_file,
        min_free_space=args.min_free_space,
        bitcoin_cli=args.bitcoin_cli,
        bitcoin_datadir=args.bitcoin_datadir,
        bitcoin_rpc_host=args.rpc_host,
        bitcoin_rpc_port=args.rpc_port,
        rpc_timeout=args.rpc_timeout,
        upload_timeout=args.upload_timeout,
        verify_timeout=args.verify_timeout,
    )


def run_pass(config: Config, cancelled: threading.Event | None = None) -> bool:
    """Archive once, stopping a node with unsafe pruning; True on success."""
    try:
        archive(config, cancelled=cancelled)
    except UnsafePruningError:
        LOG.exception("Unsafe or unverifiable Bitcoin Core pruning mode")
        try:
            stop_bitcoin(config)
        except Exception:
            LOG.exception("Bitcoin Core stop request failed")
        return False
    except Exception:
        LOG.exception("Bitcoin block archival failed")
        return False
    return cancelled is None or not cancelled.is_set()


def main(argv: list[str] | None = None) -> int:
    args = Arguments()
    build_parser().parse_args(argv, namespace=args)

    configure(verbose=args.verbose)

    config = config_from_args(args)

    if args.prune_only:
        if args.prune_after_archive or args.interval or args.core_backup is not None:
            build_parser().error(
                "--prune-only cannot be combined with uploads, intervals or core backup"
            )
        if config.prune_ceiling_file is None or config.prune_retain_from_file is None:
            build_parser().error(
                "--prune-only requires --prune-ceiling-file and --prune-retain-from-file"
            )
        from bitcoin_block_archive.prune import prune_archived_blocks
        from bitcoin_block_archive.locking import exclusive_lock

        try:
            config.state_dir.mkdir(parents=True, exist_ok=True)
            with exclusive_lock(config.lock_path) as acquired:
                if not acquired:
                    raise ArchiveError("Another archive/prune pass holds the lock")
                prune_archived_blocks(config, strict=True)
        except Exception:
            LOG.exception("Prune-only pass failed")
            return 1
        return 0

    if args.core_backup is not None:
        from bitcoin_block_archive.core_backup import main as backup_main

        return backup_main(config, args.core_backup)

    succeeded = True
    with stop_on_signals() as stop, watch_disk(config, stop) as tripped:
        if not stop.is_set():
            if args.interval == 0:
                succeeded = run_pass(config, tripped)
            else:
                run_repeatedly(lambda: run_pass(config, tripped), args.interval, stop)

    return 0 if succeeded and not tripped.is_set() else 1
