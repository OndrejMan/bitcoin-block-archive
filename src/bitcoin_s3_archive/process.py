"""Thin wrapper around subprocess used by every external command."""

from __future__ import annotations

import subprocess

from bitcoin_s3_archive.logging_setup import LOG


def run(
    command: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    LOG.debug("Running: %s", " ".join(command))

    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=True,
    )
