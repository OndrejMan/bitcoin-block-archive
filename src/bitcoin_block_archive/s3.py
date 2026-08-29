"""Uploads to S3-compatible storage via s5cmd."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.logging_setup import LOG
from bitcoin_block_archive.process import run


class Uploader(Protocol):
    """Anything able to put a local file at a remote URL."""

    def upload(self, source: Path, destination: str) -> None: ...


class S5cmdClient:
    """Uploader backed by the `s5cmd` binary."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def base_command(self) -> list[str]:
        return [
            "s5cmd",
            "--credentials-file",
            str(self._config.s3_credentials),
            "--profile",
            self._config.s3_profile,
            "--endpoint-url",
            self._config.s3_endpoint,
        ]

    def upload(self, source: Path, destination: str) -> None:
        LOG.info("Uploading %s -> %s", source, destination)

        result = run(
            [
                *self.base_command(),
                "cp",
                str(source),
                destination,
            ],
            check=False,
        )

        if result.returncode != 0:
            raise ArchiveError(
                f"s5cmd upload failed for {source}: {result.stderr.strip()}"
            )
