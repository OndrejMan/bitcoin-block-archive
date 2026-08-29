"""Control of and queries against the local Bitcoin Core node."""

from __future__ import annotations

import json
import subprocess

from bitcoin_s3_archive.config import Config
from bitcoin_s3_archive.errors import ArchiveError
from bitcoin_s3_archive.logging_setup import LOG
from bitcoin_s3_archive.process import run


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
    )


def _rpc_json(
    config: Config,
    *arguments: str,
) -> dict[str, object]:
    result = cli(config, *arguments, check=False)

    if result.returncode != 0:
        raise ArchiveError(
            f"bitcoin-cli {' '.join(arguments)} failed: "
            f"{result.stderr.strip()}"
        )

    payload = json.loads(result.stdout)

    if not isinstance(payload, dict):
        raise ArchiveError(
            f"bitcoin-cli {' '.join(arguments)} did not return an object"
        )

    return payload


def _require_int(
    payload: dict[str, object],
    key: str,
    context: str,
) -> int:
    value = payload.get(key)

    if not isinstance(value, int):
        raise ArchiveError(f"{context} did not report an integer {key!r}")

    return value


def block_height(config: Config, block_hash: str) -> int:
    """Height of any block known to the node, including stale ones."""
    payload = _rpc_json(config, "getblockheader", block_hash)

    return _require_int(payload, "height", f"getblockheader {block_hash}")


def chain_height(config: Config) -> int:
    payload = _rpc_json(config, "getblockchaininfo")

    return _require_int(payload, "blocks", "getblockchaininfo")


def prune_blockchain(config: Config, height: int) -> int:
    """Ask the node to prune up to `height`; returns the last height pruned.

    Requires `prune=1` (manual pruning mode) in bitcoin.conf. Bitcoin Core
    clamps the request to keep the most recent blocks around the tip.
    """
    result = cli(config, "pruneblockchain", str(height), check=False)

    if result.returncode != 0:
        raise ArchiveError(
            f"pruneblockchain {height} failed: {result.stderr.strip()}"
        )

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
        LOG.error(
            "Failed to stop Bitcoin Core: %s",
            result.stderr.strip(),
        )
    else:
        LOG.info("Bitcoin Core stop requested")
