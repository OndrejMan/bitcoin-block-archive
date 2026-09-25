"""Uploads to S3-compatible storage via s5cmd."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.hashing import checksum_line
from bitcoin_block_archive.jsonutil import load_object
from bitcoin_block_archive.logging_setup import LOG
from bitcoin_block_archive.process import run


class Uploader(Protocol):
    """Archive storage supporting uploads and verification before pruning."""

    def upload(self, source: Path, destination: str) -> None: ...

    def verify(self, destination: str, size: int, checksum: str) -> None: ...


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
            timeout=self._config.upload_timeout,
        )

        if result.returncode != 0:
            raise ArchiveError(
                f"s5cmd upload failed for {source}: {result.stderr.strip()}"
            )

    def verify(self, destination: str, size: int, checksum: str) -> None:
        """Check existence/size and the SHA sidecar before allowing pruning."""
        def run_verify_command(*arguments: str) -> subprocess.CompletedProcess[str]:
            return run(
                [*self.base_command(), *arguments],
                check=False,
                timeout=self._config.verify_timeout,
            )

        def verify_metadata() -> None:
            result = run_verify_command("--json", "head", destination)
            if result.returncode != 0:
                raise ArchiveError(
                    f"Cannot verify {destination}: {result.stderr.strip()}"
                )
            metadata = load_object(result.stdout, f"S3 metadata for {destination}")
            if (
                metadata.get("key") != destination
                or type(metadata.get("size")) is not int
                or metadata["size"] != size
            ):
                raise ArchiveError(f"Remote size/key mismatch for {destination}")

        def verify_checksum_sidecar() -> None:
            sidecar = run_verify_command("cat", f"{destination}.sha256")
            expected = checksum_line(checksum, destination.rsplit("/", 1)[-1])
            if sidecar.returncode != 0 or sidecar.stdout != expected:
                raise ArchiveError(
                    f"Missing or mismatched checksum sidecar for {destination}"
                )

        verify_metadata()
        verify_checksum_sidecar()
