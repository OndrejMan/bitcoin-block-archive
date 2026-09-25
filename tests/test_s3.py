from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from bitcoin_block_archive import s3
from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.models import ArchiveSidecarJSON


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


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "missing",
        "size",
        "head_json",
        "metadata_missing",
        "metadata",
        "metadata_json",
        "version_bool",
        "version_float",
        "range_float",
        "checksum",
    ],
)
def test_remote_verification(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    url = "s3://bucket/blk00000.dat"
    metadata: ArchiveSidecarJSON = {
        "schema_version": 1,
        "file": "blk00000.dat",
        "size": 42,
        "sha256": "a" * 64,
        "height_ranges": [[100, 120]],
    }
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
                "invalid JSON" if failure == "head_json" else payload,
                "not found",
            )
        assert command[-2:] == ["cat", f"{url}.json"]
        remote_metadata = dict(metadata)
        if failure == "metadata":
            remote_metadata["height_ranges"] = [[100, 119]]
        if failure == "version_bool":
            remote_metadata["schema_version"] = True
        if failure == "version_float":
            remote_metadata["schema_version"] = 1.0
        if failure == "range_float":
            remote_metadata["height_ranges"] = [[100.0, 120.0]]
        if failure == "checksum":
            remote_metadata["sha256"] = "b" * 64
        return subprocess.CompletedProcess(
            command,
            1 if failure == "metadata_missing" else 0,
            "invalid JSON"
            if failure == "metadata_json"
            else json.dumps(remote_metadata),
            "",
        )

    monkeypatch.setattr(s3, "run", fake_run)
    client = s3.S5cmdClient(replace(config, verify_timeout=12))
    if failure is None:
        client.verify(url, metadata)
        assert len(commands) == 2
        assert commands[0][-3:] == ["--json", "head", url]
    else:
        with pytest.raises(ArchiveError):
            client.verify(url, metadata)
