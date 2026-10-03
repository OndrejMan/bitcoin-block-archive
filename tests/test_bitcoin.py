from __future__ import annotations

import json
import subprocess
from dataclasses import replace

import pytest

from bitcoin_block_archive import bitcoin
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError

MANUAL = {"pruned": True, "automatic_pruning": False}
AUTOMATIC = {"pruned": True, "automatic_pruning": True}
UNPRUNED = {"pruned": False}
UNKNOWN = {"pruned": True}


@pytest.mark.parametrize(
    "info,archiver_prunes,error",
    [
        (MANUAL, True, None),
        (MANUAL, False, None),
        (UNPRUNED, False, None),
        (UNPRUNED, True, "requires Bitcoin Core prune=1"),
        (AUTOMATIC, True, "must not prune automatically"),
        (AUTOMATIC, False, "must not prune automatically"),
        (UNKNOWN, False, "must not prune automatically"),
        ({}, False, "must not prune automatically"),
    ],
)
def test_only_manual_or_no_pruning_is_safe(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    info: dict[str, bool],
    archiver_prunes: bool,
    error: str | None,
) -> None:
    config = replace(config, rpc_timeout=7)

    def fake_run(
        command: list[str],
        *,
        check: bool = True,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert timeout == 7
        assert command[-1] == "getblockchaininfo"
        return subprocess.CompletedProcess(command, 0, json.dumps(info), "")

    monkeypatch.setattr(bitcoin, "run", fake_run)
    if error is None:
        bitcoin.require_safe_pruning(config, archiver_prunes=archiver_prunes)
    else:
        with pytest.raises(ArchiveError, match=error):
            bitcoin.require_safe_pruning(config, archiver_prunes=archiver_prunes)


@pytest.mark.parametrize("height", [True, "123", None])
def test_rpc_height_rejects_non_integer_json_values(
    config: Config, monkeypatch: pytest.MonkeyPatch, height: object
) -> None:
    def fake_run(
        command: list[str], *, check: bool = True, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"height": height}), ""
        )

    monkeypatch.setattr(bitcoin, "run", fake_run)
    with pytest.raises(ArchiveError, match="integer"):
        bitcoin.block_height(config, "a" * 64)


def test_failed_stop_is_reported_to_the_caller(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed_run(
        command: list[str], *, check: bool = True, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert command[-1] == "stop"
        return subprocess.CompletedProcess(command, 1, "", "RPC unavailable")

    monkeypatch.setattr(bitcoin, "run", failed_run)
    with pytest.raises(ArchiveError, match="RPC unavailable"):
        bitcoin.stop_bitcoin(config)


def record_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(
        command: list[str], *, check: bool = True, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(bitcoin, "run", fake_run)
    return commands


def test_local_node_uses_bitcoin_cli_defaults(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = record_commands(monkeypatch)

    bitcoin.cli(config, "getblockchaininfo")

    assert commands == [
        [
            "bitcoin-cli",
            f"-datadir={config.bitcoin_datadir}",
            "getblockchaininfo",
        ]
    ]


def test_remote_node_is_addressed_by_host_and_port(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(config, bitcoin_rpc_host="bitcoin-core", bitcoin_rpc_port=8332)
    commands = record_commands(monkeypatch)

    bitcoin.cli(config, "getblockchaininfo")

    assert commands == [
        [
            "bitcoin-cli",
            f"-datadir={config.bitcoin_datadir}",
            "-rpcconnect=bitcoin-core",
            "-rpcport=8332",
            "getblockchaininfo",
        ]
    ]
