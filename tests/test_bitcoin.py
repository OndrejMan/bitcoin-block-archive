from __future__ import annotations

import json
import subprocess
from dataclasses import replace

import pytest

from bitcoin_block_archive import bitcoin
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError


@pytest.mark.parametrize(
    "info,manual",
    [
        ({"pruned": True, "automatic_pruning": False}, True),
        ({"pruned": True, "automatic_pruning": True}, False),
        ({"pruned": False}, False),
        ({"pruned": True}, False),
    ],
)
def test_manual_pruning_uses_rpc_state(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    info: dict[str, bool],
    manual: bool,
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
    assert bitcoin.manual_pruning_enabled(config) is manual
    if manual:
        bitcoin.require_manual_pruning(config)
    else:
        with pytest.raises(ArchiveError, match="prune=1"):
            bitcoin.require_manual_pruning(config)


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
