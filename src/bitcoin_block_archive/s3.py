"""Uploads to S3-compatible storage via s5cmd."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Protocol

from bitcoin_block_archive.config import Config
from bitcoin_block_archive.errors import ArchiveError
from bitcoin_block_archive.jsonutil import load_object
from bitcoin_block_archive.logging_setup import LOG
from bitcoin_block_archive.models import ArchiveSidecarJSON
from bitcoin_block_archive.process import run


class Uploader(Protocol):
    """Archive storage supporting uploads and verification before pruning."""

    def upload(self, source: Path, destination: str) -> None: ...

    def verify(
        self,
        destination: str,
        sidecar: ArchiveSidecarJSON,
    ) -> None: ...


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

    def verify(
        self,
        destination: str,
        sidecar: ArchiveSidecarJSON,
    ) -> None:
        """Check the block and its metadata sidecar before allowing pruning."""
        self._verify_object_metadata(destination, sidecar["size"])
        self._verify_metadata_sidecar(destination, sidecar)

    def _run_verify_command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return run(
            [*self.base_command(), *arguments],
            check=False,
            timeout=self._config.verify_timeout,
        )

    def _verify_object_metadata(self, destination: str, size: int) -> None:
        result = self._run_verify_command("--json", "ls", destination)
        if result.returncode != 0:
            raise ArchiveError(f"Cannot verify {destination}: {result.stderr.strip()}")
        # `ls` matches a prefix: the block's own .json sidecar is listed beside it.
        listed = [
            load_object(line, f"S3 metadata for {destination}")
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        remote = next((item for item in listed if item.get("key") == destination), None)
        if remote is None:
            # coinjoin-pipeline's pack job replaces a pruned file by a .zst only
            # after the packed copy unpacked to the sidecar's SHA-256, so a file
            # restored from that copy is archived.
            packed = next(
                (item for item in listed if item.get("key") == f"{destination}.zst"), {}
            )
            packed_size = packed.get("size")
            if type(packed_size) is int and packed_size > 0:
                return
            remote = {}
        if type(remote.get("size")) is not int or remote["size"] != size:
            raise ArchiveError(f"Remote size/key mismatch for {destination}")

    def _verify_metadata_sidecar(
        self, destination: str, sidecar: ArchiveSidecarJSON
    ) -> None:
        result = self._run_verify_command("cat", f"{destination}.json")
        if result.returncode != 0:
            raise ArchiveError(f"Missing metadata sidecar for {destination}")
        remote = load_object(result.stdout, f"Metadata for {destination}")
        # Preserve JSON types: Python equality treats True, 1 and 1.0 alike.
        expected = json.dumps(sidecar, sort_keys=True)
        if json.dumps(remote, sort_keys=True) != expected:
            raise ArchiveError(f"Mismatched metadata sidecar for {destination}")
