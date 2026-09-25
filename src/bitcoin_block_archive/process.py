"""Thin wrapper around subprocess used by every external command."""

from __future__ import annotations

import subprocess

from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.logging_setup import LOG


def run(
    command: list[str],
    *,
    timeout: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    LOG.debug("Running: %s", " ".join(command))

    try:
        return subprocess.run(
            command,
            check=check,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise ArchiveError(
            f"{command[0]} exceeded its {timeout}s time limit"
        ) from error
