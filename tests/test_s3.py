from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_block_archive import s3
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError


def test_base_command_carries_credentials(config: Config) -> None:
    command = s3.S5cmdClient(config).base_command()

    assert command[0] == "s5cmd"
    assert "--profile" in command
    assert command[command.index("--profile") + 1] == "testing"
    assert command[command.index("--endpoint-url") + 1] == (
        "https://s3.example.invalid"
    )


def test_upload_raises_on_failure(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        command: list[str],
        *,
        check: bool = True,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert timeout == 321
        return subprocess.CompletedProcess(command, 1, "", "denied\n")

    monkeypatch.setattr(s3, "run", fake_run)

    with pytest.raises(ArchiveError, match="denied"):
        s3.S5cmdClient(replace(config, upload_timeout=321)).upload(
            Path("blk.dat"), "s3://bucket/blk.dat"
        )


@pytest.mark.parametrize("failure", [None, "missing", "size", "sidecar", "json"])
def test_remote_verification(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    url = "s3://bucket/blk00000.dat"
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        check: bool = True,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert timeout == 12
        commands.append(command)
        if "head" in command:
            payload = json.dumps({"key": url, "size": 43 if failure == "size" else 42})
            return subprocess.CompletedProcess(
                command,
                1 if failure == "missing" else 0,
                "invalid JSON" if failure == "json" else payload,
                "not found",
            )
        assert command[-2:] == ["cat", f"{url}.sha256"]
        return subprocess.CompletedProcess(
            command,
            0,
            "bad" if failure == "sidecar" else "a" * 64 + "  blk00000.dat\n",
            "",
        )

    monkeypatch.setattr(s3, "run", fake_run)
    client = s3.S5cmdClient(replace(config, verify_timeout=12))
    if failure is None:
        client.verify(url, 42, "a" * 64)
        assert len(commands) == 2
        assert commands[0][-3:] == ["--json", "head", url]
    else:
        with pytest.raises(ArchiveError):
            client.verify(url, 42, "a" * 64)
