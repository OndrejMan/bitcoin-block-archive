"""Control of and queries against the local Bitcoin Core node."""

from __future__ import annotations

import subprocess

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.jsonutil import load_object, require_int
from bitcoin_block_archive.logging_setup import LOG
from bitcoin_block_archive.process import run


def cli(
    config: Config,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(
        [
            config.bitcoin_cli,
            f"-datadir={config.bitcoin_datadir}",
            *arguments,
        ],
        check=check,
        timeout=config.rpc_timeout,
    )


def _rpc_json(
    config: Config,
    *arguments: str,
) -> dict[str, object]:
    result = cli(config, *arguments, check=False)

    if result.returncode != 0:
        raise ArchiveError(
            f"bitcoin-cli {' '.join(arguments)} failed: {result.stderr.strip()}"
        )

    return load_object(result.stdout, f"bitcoin-cli {' '.join(arguments)}")


def _require_int(
    payload: dict[str, object],
    key: str,
    context: str,
) -> int:
    return require_int(payload.get(key), f"{context} {key!r}")


def block_height(config: Config, block_hash: str) -> int:
    """Height of any block known to the node, including stale ones."""
    payload = _rpc_json(config, "getblockheader", block_hash)

    return _require_int(payload, "height", f"getblockheader {block_hash}")


def chain_height(config: Config) -> int:
    payload = _rpc_json(config, "getblockchaininfo")

    return _require_int(payload, "blocks", "getblockchaininfo")


def manual_pruning_enabled(config: Config) -> bool:
    info = _rpc_json(config, "getblockchaininfo")
    return info.get("pruned") is True and info.get("automatic_pruning") is False


def require_manual_pruning(config: Config) -> None:
    if not manual_pruning_enabled(config):
        raise ArchiveError("Archive-driven pruning requires Bitcoin Core prune=1")


def prune_blockchain(config: Config, height: int) -> int:
    """Ask the node to prune up to `height`; returns the last height pruned.

    Requires `prune=1` (manual pruning mode) in bitcoin.conf. Bitcoin Core
    clamps the request to keep the most recent blocks around the tip.
    """
    result = cli(config, "pruneblockchain", str(height), check=False)

    if result.returncode != 0:
        raise ArchiveError(f"pruneblockchain {height} failed: {result.stderr.strip()}")

    try:
        return int(result.stdout.strip())
    except ValueError as error:
        raise ArchiveError(
            f"pruneblockchain returned {result.stdout.strip()!r}"
        ) from error


def stop_bitcoin(config: Config) -> None:
    LOG.critical("Stopping Bitcoin Core to prevent pruning of unarchived data")

    result = cli(config, "stop", check=False)

    if result.returncode != 0:
        raise ArchiveError(f"Failed to stop Bitcoin Core: {result.stderr.strip()}")
    LOG.info("Bitcoin Core stop requested")
