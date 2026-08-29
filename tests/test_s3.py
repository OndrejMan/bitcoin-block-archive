from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bitcoin_s3_archive import s3
from bitcoin_s3_archive.config import Config
from bitcoin_s3_archive.errors import ArchiveError


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
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "denied\n")

    monkeypatch.setattr(s3, "run", fake_run)

    with pytest.raises(ArchiveError, match="denied"):
        s3.S5cmdClient(config).upload(Path("blk.dat"), "s3://bucket/blk.dat")
