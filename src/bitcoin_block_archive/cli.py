"""Command line interface."""

from __future__ import annotations

import argparse
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
    DEFAULT_S3_DESTINATION,
    DEFAULT_S3_ENDPOINT,
    DEFAULT_S3_PROFILE,
    DEFAULT_STATE_DIR,
    Config,
    default_credentials,
)
from bitcoin_block_archive.disk import format_size, free_bytes, parse_size
from bitcoin_block_archive.logging_setup import LOG, configure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bitcoin-block-archive",
        description=(
            "Archive completed Bitcoin Core blk*.dat files "
            "to S3 using s5cmd."
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
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
        default=DEFAULT_S3_ENDPOINT,
    )

    parser.add_argument(
        "--profile",
        default=DEFAULT_S3_PROFILE,
    )

    parser.add_argument(
        "--credentials",
        type=Path,
        default=default_credentials(),
    )

    parser.add_argument(
        "--destination",
        default=DEFAULT_S3_DESTINATION,
    )

    parser.add_argument(
        "--keep-latest-files",
        type=int,
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
        default=DEFAULT_BITCOIN_DATADIR,
    )

    parser.add_argument(
        "--prune-after-archive",
        action="store_true",
        help=(
            "After a successful pass, call `pruneblockchain` up to the "
            "highest height that cannot delete an unarchived block file. "
            "Requires prune=1 (manual pruning) in bitcoin.conf."
        ),
    )

    parser.add_argument(
        "--min-free-space",
        type=parse_size,
        default=DEFAULT_MIN_FREE_SPACE,
        metavar="SIZE",
        help=(
            "Stop Bitcoin Core when free space in the block directory falls "
            "below SIZE (e.g. 20G). 0 disables the watchdog."
        ),
    )

    parser.add_argument(
        "--no-stop-on-error",
        action="store_true",
        help=(
            "Never stop Bitcoin Core, whatever happens. By default it is "
            "stopped when archival fails under automatic pruning, or when "
            "--min-free-space is breached."
        ),
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
    )

    return parser


def config_from_args(args: argparse.Namespace) -> Config:
    return Config(
        block_dir=args.block_dir,
        state_dir=args.state_dir,
        s3_endpoint=args.endpoint,
        s3_profile=args.profile,
        s3_credentials=args.credentials,
        s3_destination=args.destination,
        keep_latest_files=args.keep_latest_files,
        stop_bitcoin_on_error=not args.no_stop_on_error,
        prune_after_archive=args.prune_after_archive,
        min_free_space=args.min_free_space,
        bitcoin_cli=args.bitcoin_cli,
        bitcoin_datadir=args.bitcoin_datadir,
    )


def _space_is_critical(config: Config) -> bool:
    if config.min_free_space <= 0 or not config.block_dir.is_dir():
        return False

    free = free_bytes(config.block_dir)

    if free >= config.min_free_space:
        return False

    LOG.critical(
        "Only %s free in %s, below the %s threshold",
        format_size(free),
        config.block_dir,
        format_size(config.min_free_space),
    )

    return True


def should_stop_bitcoin(config: Config, *, failed: bool) -> bool:
    """Decide whether the node has to be stopped to protect unarchived data."""
    if not config.stop_bitcoin_on_error:
        return False

    if _space_is_critical(config):
        return True

    if not failed:
        return False

    if config.prune_after_archive:
        # Manual pruning mode: nothing is deleted unless this tool asks for
        # it, so a failed pass costs disk space, not blocks.
        LOG.warning(
            "Archival failed; leaving Bitcoin Core running because manual "
            "pruning cannot delete unarchived blocks"
        )
        return False

    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    configure(verbose=args.verbose)

    config = config_from_args(args)

    failed = False

    try:
        archive(config)

    except Exception:
        LOG.exception("Bitcoin block archival failed")
        failed = True

    if should_stop_bitcoin(config, failed=failed):
        stop_bitcoin(config)

    return 1 if failed else 0
