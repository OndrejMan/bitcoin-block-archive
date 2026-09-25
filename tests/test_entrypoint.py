from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "docker-entrypoint.sh"

CREDENTIAL_VARIABLES = (
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_PROFILE",
)


@pytest.fixture
def fake_archiver(tmp_path: Path) -> Path:
    """Stand-in for the installed CLI that prints the arguments it receives."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "bitcoin-block-archive"
    script.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    script.chmod(0o755)
    return bin_dir


def run_entrypoint(
    fake_archiver: Path,
    tmp_path: Path,
    *arguments: str,
    **environment: str,
) -> subprocess.CompletedProcess[str]:
    base = {
        name: value
        for name, value in os.environ.items()
        if name not in CREDENTIAL_VARIABLES
    }
    return subprocess.run(
        ["sh", str(ENTRYPOINT), *arguments],
        env={
            **base,
            "PATH": f"{fake_archiver}:{os.environ['PATH']}",
            "S3_CREDENTIALS_FILE": str(tmp_path / "credentials"),
            **environment,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_without_secrets_arguments_pass_through(
    fake_archiver: Path, tmp_path: Path
) -> None:
    result = run_entrypoint(fake_archiver, tmp_path, "--block-dir", "/bitcoin/blocks")

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["--block-dir", "/bitcoin/blocks"]
    assert not (tmp_path / "credentials").exists()


def test_secrets_become_a_private_credentials_profile(
    fake_archiver: Path, tmp_path: Path
) -> None:
    credentials = tmp_path / "credentials"
    result = run_entrypoint(
        fake_archiver,
        tmp_path,
        "--state-dir",
        "/state",
        S3_ACCESS_KEY_ID="access",
        S3_SECRET_ACCESS_KEY="secret",
        S3_PROFILE="archive",
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "--credentials",
        str(credentials),
        "--profile",
        "archive",
        "--state-dir",
        "/state",
    ]
    assert credentials.read_text() == (
        "[archive]\naws_access_key_id = access\naws_secret_access_key = secret\n"
    )
    assert stat.S_IMODE(credentials.stat().st_mode) == 0o600


def test_half_a_key_pair_is_rejected(fake_archiver: Path, tmp_path: Path) -> None:
    result = run_entrypoint(fake_archiver, tmp_path, S3_ACCESS_KEY_ID="access")

    assert result.returncode == 2
    assert "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY" in result.stderr
    assert result.stdout == ""
